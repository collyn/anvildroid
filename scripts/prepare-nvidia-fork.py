#!/usr/bin/env python3
"""Assemble immutable NVIDIA archives from pinned fork CI artifacts and prebuilts."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
module = ROOT / 'services/runtime-nvidia.py'
if not module.is_file(): module = ROOT / 'runtime-nvidia.py'
spec = importlib.util.spec_from_file_location('fork_nvidia', module)
nvidia = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nvidia)
ARTIFACT_FILES = {
    'host-venus-server': nvidia.PAYLOAD_FILES['host'],
    'guest-vulkan-virtio-x86': ('vendor/lib/hw/vulkan.virtio.so',),
    'guest-vulkan-virtio-x86_64': ('vendor/lib64/hw/vulkan.virtio.so',),
    'guest-gralloc': ('vendor/lib64/libgbm_mesa_wrapper.so',),
    'guest-hwcomposer': ('vendor/lib64/hw/hwcomposer.waydroid.so',),
    'guest-angle': tuple(p for group in ('guest32', 'guest64')
                         for p in nvidia.PAYLOAD_FILES[group] if '/egl/' in p) + ('LICENSE', 'NOTICE'),
}
PREBUILT_FILES = tuple(p for group in ('guest32', 'guest64', 'surfaceflinger')
                       for p in nvidia.PAYLOAD_FILES[group]
                       if '/egl/' in p or p == 'system/bin/surfaceflinger')
ARCHIVE_MTIME = 1791802800


def verified_bytes(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError('Input checksum mismatch: ' + path.name)
    return data


def download(inputs):
    inputs.mkdir(parents=True, exist_ok=True)
    for name, pin in nvidia.FORK_SOURCE['artifacts'].items():
        destination = inputs / (name + '.zip')
        if destination.exists():
            verified_bytes(destination, pin['sha256'])
            continue
        with tempfile.TemporaryDirectory(prefix='.artifact-', dir=inputs) as directory:
            staged = Path(directory) / destination.name
            with staged.open('xb') as stream:
                subprocess.run(['gh', 'api', f"repos/{nvidia.UPSTREAM_REPO}/actions/artifacts/{pin['id']}/zip"],
                               stdout=stream, check=True, timeout=300)
            verified_bytes(staged, pin['sha256'])
            os.link(staged, destination)
    pin = nvidia.FORK_SOURCE['prebuilts']
    name = f"waydroid-nvidia-guest-prebuilts-{pin['version']}.tar.gz"
    destination = inputs / name
    if destination.exists():
        verified_bytes(destination, pin['sha256'])
        return
    with tempfile.TemporaryDirectory(prefix='.prebuilts-', dir=inputs) as directory:
        staged = Path(directory) / name
        url = f"https://github.com/{pin['repo']}/releases/download/{pin['version']}/{name}"
        subprocess.run(['curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
                        '--retry', '2', '--max-time', '300', '--output', str(staged), url],
                       check=True, timeout=920)
        verified_bytes(staged, pin['sha256'])
        os.link(staged, destination)


def zip_files(data, allowed):
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for item in archive.infolist():
            name = item.filename
            kind = stat.S_IFMT(item.external_attr >> 16)
            if name.startswith('/') or '..' in Path(name).parts or kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise RuntimeError('Unsafe artifact member: ' + name)
            if item.is_dir(): continue
            if name not in allowed or name in files or item.file_size > 256 * 1024**2:
                raise RuntimeError('Unexpected artifact member: ' + name)
            files[name] = archive.read(item)
    if set(files) != set(allowed): raise RuntimeError('Incomplete fork artifact')
    return files


def collect(inputs):
    host, guest, angle = {}, {}, {}
    for name, pin in nvidia.FORK_SOURCE['artifacts'].items():
        allowed = ARTIFACT_FILES[name]
        data = verified_bytes(inputs / (name + '.zip'), pin['sha256'])
        files = zip_files(data, allowed)
        (host if name == 'host-venus-server' else
         angle if name == 'guest-angle' else guest).update(files)
    pin = nvidia.FORK_SOURCE['prebuilts']
    data = verified_bytes(inputs / f"waydroid-nvidia-guest-prebuilts-{pin['version']}.tar.gz", pin['sha256'])
    prebuilts = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        errors = nvidia.validate_archive_members(archive.getmembers())
        if errors: raise RuntimeError('; '.join(errors))
        for item in archive:
            name = str(Path(item.name))
            if not item.isfile() or name not in PREBUILT_FILES: continue
            if name in prebuilts or item.size > 256 * 1024**2:
                raise RuntimeError('Unexpected prebuilt member: ' + name)
            prebuilts[name] = archive.extractfile(item).read()
    if set(prebuilts) != set(PREBUILT_FILES): raise RuntimeError('Incomplete upstream prebuilts')
    if nvidia.FORK_SOURCE.get('angle') and not angle:
        raise RuntimeError('Pinned ASTC-enabled ANGLE artifact is required')
    # Apply last: verified upstream fallback must never replace our ASTC build.
    prebuilts.update(angle)
    # Verify actual ELF machines/classes using the controller validator before
    # emitting archives. Never extract untrusted member paths to the filesystem.
    with tempfile.TemporaryDirectory(prefix='fork-elf-check-') as directory:
        root = Path(directory)
        for group, files in (('', host), ('guest/', guest | prebuilts)):
            for name, data in files.items():
                path = root / (group + name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        report = nvidia.validate_payload(root)
        if not report['ok']: raise RuntimeError('; '.join(report['errors']))
    return {'host-x86_64': host, 'guest-android-x86_64': guest, 'guest-prebuilts': prebuilts}


def write_archive(files, destination, component):
    with destination.open('xb') as stream:
        with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=ARCHIVE_MTIME) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for name, data in sorted(files.items()):
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    member.mode = 0o755 if component == 'host-x86_64' or name == 'system/bin/surfaceflinger' else 0o644
                    member.mtime = ARCHIVE_MTIME
                    archive.addfile(member, io.BytesIO(data))


def prepare(inputs, output):
    groups = collect(inputs)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.fork-payload-', dir=output) as directory:
        staged = []
        for component, files in groups.items():
            name = f'waydroid-nvidia-{component}-{nvidia.UPSTREAM_VERSION}.tar.gz'
            destination = output / name
            if destination.exists():
                verified_bytes(destination, nvidia.ARCHIVES[component])
                continue
            archive = Path(directory) / name
            write_archive(files, archive, component)
            verified_bytes(archive, nvidia.ARCHIVES[component])
            staged.append((archive, destination))
        for archive, destination in staged:
            os.link(archive, destination)
    print('Verified fork payload archives:', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, default=ROOT / 'target/tools' / f'nvidia-{nvidia.UPSTREAM_VERSION}-inputs')
    parser.add_argument('--output', type=Path, default=ROOT / 'target/tools/nvidia')
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    if args.download: download(args.inputs)
    prepare(args.inputs, args.output)
