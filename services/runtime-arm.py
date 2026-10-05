"""Native bridge payloads with isolated per-runtime configuration."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile
import importlib.util

_source_spec = importlib.util.spec_from_file_location('runtime_arm_source', Path(__file__).with_name('runtime-arm-source.py'))
_source = importlib.util.module_from_spec(_source_spec)
_source_spec.loader.exec_module(_source)
CATALOG = _source.CATALOG


def describe(files, digest):
    engines = [engine for engine, library in (('libndk', 'libndk_translation.so'), ('libhoudini', 'libhoudini.so'))
               if all('system/' + lib + '/' + library in files for lib in ('lib', 'lib64'))]
    if len(engines) != 1:
        raise RuntimeError('Incomplete or mixed ARM payload: choose one engine with both 32-bit and 64-bit bridge libraries')
    engine = engines[0]
    known = next((item for item in CATALOG if item['sha256'] == digest), None)
    if known and known['engine'] != engine:
        raise RuntimeError('ARM engine does not match catalog version')
    return dict(known or {'engine': engine, 'version': 'Custom ' + digest[:12],
                         'label': engine + ' / custom ' + digest[:12], 'android': 'Custom; compatibility unverified'},
                engine=engine)

SHA256 = 'a142d1586c9eafb5edf62277110f2128bc03066179a6783bcaa33ee322e1cbd0'
COMMIT = '68734c52556d3d7a6db34c603dd9276915c29f2f'
PREFIX = 'vendor_google_proprietary_ndk_translation-prebuilt-' + COMMIT + '/prebuilts/'
PROPERTIES = {
    'ro.product.cpu.abilist': 'x86_64,x86,arm64-v8a,armeabi-v7a,armeabi',
    'ro.product.cpu.abilist32': 'x86,armeabi-v7a,armeabi',
    'ro.product.cpu.abilist64': 'x86_64,arm64-v8a',
    'ro.dalvik.vm.native.bridge': 'libndk_translation.so',
    'ro.ndk_translation.version': '0.2.3',
    'ro.dalvik.vm.isa.arm': 'x86', 'ro.dalvik.vm.isa.arm64': 'x86_64',
}


def _payload_prefix(names):
    candidates = set()
    for name in names:
        path = PurePosixPath(name)
        parts = path.parts
        if 'prebuilts' in parts:
            candidates.add('/'.join(parts[:parts.index('prebuilts') + 1]) + '/')
    return next(iter(candidates), '') if len(candidates) == 1 else None


def stage(archive, destination, expected_sha256=None):
    """Verify before extraction; do not install binfmt handlers shared with host."""
    expected_sha256 = expected_sha256 or SHA256
    with Path(archive).open('rb') as stream: blob = stream.read(256 * 1024**2 + 1)
    digest = hashlib.sha256(blob).hexdigest()
    if len(blob) > 256 * 1024**2 or (expected_sha256 and digest != expected_sha256):
        raise RuntimeError('ARM translation archive checksum mismatch')
    if destination.exists(): raise RuntimeError('ARM payload already exists')
    staging = Path(tempfile.mkdtemp(prefix='.arm-', dir=destination.parent))
    try:
        hashes = {}
        try:
            package = zipfile.ZipFile(io.BytesIO(blob))
        except (zipfile.BadZipFile, OSError) as error:
            raise RuntimeError('ARM translation archive checksum mismatch or invalid ZIP') from error
        with package:
            if len(package.infolist()) > 1024: raise RuntimeError('ARM archive has too many entries')
            names = [entry.filename for entry in package.infolist() if not entry.is_dir()]
            prefix = _payload_prefix(names)
            total = 0
            for entry in package.infolist():
                if entry.is_dir(): continue
                filename = entry.filename
                if filename.startswith(PREFIX): relative = PurePosixPath(filename[len(PREFIX):])
                elif prefix and filename.startswith(prefix): relative = PurePosixPath(filename[len(prefix):])
                elif filename.startswith('system/'): relative = PurePosixPath(filename[len('system/'):])
                elif PurePosixPath(filename).parts[:1] in (('lib',), ('lib64',), ('bin',), ('etc',)): relative = PurePosixPath(filename)
                else: continue
                if relative.is_absolute() or '..' in relative.parts or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG):
                    raise RuntimeError('Unsafe ARM archive entry')
                # JNI translation needs libraries, not host-global binfmt registrations.
                if str(relative).startswith(('etc/binfmt_misc/', 'etc/init/')): continue
                total += entry.file_size
                if total > 512 * 1024**2: raise RuntimeError('ARM payload exceeds size limit')
                target = staging / 'system' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                data = package.read(entry)
                with target.open('xb') as output:
                    output.write(data); output.flush(); os.fsync(output.fileno())
                target.chmod(0o755 if relative.parts[0] == 'bin' else 0o644)
                hashes[str(PurePosixPath('system') / relative)] = hashlib.sha256(data).hexdigest()
        metadata = describe(hashes, digest)
        for directory, _, _ in os.walk(staging): Path(directory).chmod(0o755)
        with (staging / 'manifest.json').open('x') as output:
            json.dump({'archive_sha256': digest, 'files': hashes, 'engine': metadata['engine'],
                       'version': metadata['version']}, output)
            output.flush(); os.fsync(output.fileno())
        (staging / 'manifest.json').chmod(0o644)
        os.rename(staging, destination)
    finally:
        if staging.exists(): shutil.rmtree(staging)


def verify(directory, expected_sha256=None):
    directory = Path(directory)
    for item in (directory, directory / 'manifest.json'):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Untrusted ARM payload metadata')
    manifest = json.loads((directory / 'manifest.json').read_text())
    archive_sha256 = manifest.get('archive_sha256')
    if not isinstance(archive_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', archive_sha256):
        raise RuntimeError('Unknown ARM translation artifact')
    if expected_sha256 and archive_sha256 != expected_sha256: raise RuntimeError('ARM translation archive checksum mismatch')
    files = manifest.get('files')
    if not isinstance(files, dict):
        raise RuntimeError('Incomplete ARM payload manifest')
    metadata = describe(files, archive_sha256)
    if manifest.get('engine', metadata['engine']) != metadata['engine']:
        raise RuntimeError('ARM manifest engine mismatch')
    for name, expected in files.items():
        path = directory / name
        if path.is_symlink() or '..' in PurePosixPath(name).parts or PurePosixPath(name).is_absolute(): raise RuntimeError('Unsafe ARM payload path')
        for parent in path.parents:
            if parent == directory: break
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError('Untrusted ARM payload directory')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('ARM payload integrity check failed')
    return manifest


def runtime_properties(instance):
    config = read_config(instance)
    if not config: return None
    if not config['enabled']:
        return {'ro.product.cpu.abilist': 'x86_64,x86', 'ro.product.cpu.abilist32': 'x86',
                'ro.product.cpu.abilist64': 'x86_64', 'ro.dalvik.vm.native.bridge': '0'}
    manifest = verify(payload_path(instance, config['archive_sha256']), config['archive_sha256'])
    metadata = describe(manifest['files'], manifest['archive_sha256'])
    props = dict(PROPERTIES)
    if metadata['engine'] == 'libhoudini':
        props['ro.dalvik.vm.native.bridge'] = 'libhoudini.so'
        props.pop('ro.ndk_translation.version', None)
    return props

ARCHIVE = Path('/usr/local/lib/anvildroid-controller/libndk-0.2.3.zip')


def read_config(instance):
    path = instance / 'arm.json'
    if not path.exists(): return None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 4096:
        raise RuntimeError('Unsafe ARM configuration')
    config = json.loads(path.read_text())
    if set(config) not in ({'enabled', 'archive_sha256', 'images'}, {'enabled', 'archive_sha256', 'images', 'provider'}) or type(config['enabled']) is not bool:
        raise RuntimeError('Invalid ARM configuration')
    if not isinstance(config['archive_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', config['archive_sha256']):
        raise RuntimeError('Invalid ARM configuration checksum')
    if config['images'] != json.loads((instance / 'support/images.json').read_text()):
        raise RuntimeError('ARM configuration does not match runtime images')
    return config


def active_layer(instance):
    config = read_config(instance)
    if not config or not config['enabled']: return None
    payload = payload_path(instance, config['archive_sha256'])
    verify(payload, config['archive_sha256'])
    return payload


def payload_path(instance, digest):
    stored = instance / 'arm-payloads' / digest
    return stored if stored.exists() else instance / 'arm-payload'


def info(instance, supported, sources=None):
    config = read_config(instance) if instance.exists() else None
    props = instance / 'support/waydroid.prop'
    bridge = next((line.split('=', 1)[1] for line in props.read_text().splitlines()
                   if line.startswith('ro.dalvik.vm.native.bridge=')), '') if props.exists() else ''
    inherited = not config and bridge in ('libndk_translation.so', 'libhoudini.so')
    available = ARCHIVE.is_file()
    configured_provider = config.get('provider', 'libndk_translation 0.2.3') if config else bridge.removesuffix('.so') if inherited else None
    return {'provider': configured_provider or 'libndk_translation 0.2.3', 'archive_sha256': config.get('archive_sha256') if config else None,
            'enabled': bool(config and config['enabled']) or inherited,
            'inherited': inherited, 'supported': supported, 'available': available,
            'sources': sources or [],
            'experimental': True, 'abis': ['armeabi-v7a', 'arm64-v8a']}


def configure(instance, enabled, images, atomic, archive=None, provider='libndk_translation 0.2.3'):
    """Caller must own prepared runtime and hold its stopped-state guard."""
    previous = read_config(instance)
    digest = (previous or {}).get('archive_sha256', SHA256)
    if enabled:
        source = archive or ARCHIVE
        expected = SHA256 if source == ARCHIVE else hashlib.sha256(source.read_bytes()).hexdigest()
        payload = payload_path(instance, expected)
        if payload.exists():
            current = verify(payload)['archive_sha256']
            if current != expected:
                directory = instance / 'arm-payloads'
                directory.mkdir(mode=0o755, exist_ok=True)
                directory.chmod(0o755)
                payload = directory / expected
        if not payload.exists(): stage(source, payload, expected)
        manifest = verify(payload, expected)
        provider = describe(manifest['files'], expected)['label']
        digest = expected
    elif previous:
        provider = previous.get('provider', provider)
    atomic(instance / 'arm.json', {'enabled': enabled, 'archive_sha256': digest, 'images': images, 'provider': provider})
