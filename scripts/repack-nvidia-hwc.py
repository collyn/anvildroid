#!/usr/bin/env python3
"""Reproduce the local pitch-bytes pin from verified base archives and a built HWC."""
import argparse
import gzip
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nvidia', ROOT / 'services/runtime-nvidia.py')
nvidia = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nvidia)
BASE_VERSION = 'devfix-20261010-vkr02726'
BASE_HASHES = {
    'host-x86_64': '785a3a8b7f6ace07e497ef5e8975259828fddebca7174b70f9f33d9538c128b5',
    'guest-android-x86_64': '3e41c050e1c1fb0f319f870c80e196f33f4195914503ff0c6818c5e8078d26d5',
    'guest-prebuilts': 'ad557bde6a98a6d322c7e7a246b03a88d48d1d3d31bacb05480dff9332ae7a0f',
}
# Fixed archive timestamp from this local pin, to make the gzip hash repeatable.
ARCHIVE_MTIME = 1791797685
HWC = 'vendor/lib64/hw/hwcomposer.waydroid.so'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def replace_hwc(source, hwc, stream, name):
    with gzip.GzipFile(filename=name[:-3], mode='wb', fileobj=stream,
                       mtime=ARCHIVE_MTIME) as compressed:
        with tarfile.open(source, 'r:gz') as src, tarfile.open(fileobj=compressed, mode='w') as tar:
            errors = nvidia.validate_archive_members(src.getmembers())
            if errors:
                raise RuntimeError('; '.join(errors))
            replaced = 0
            for member in src:
                if str(Path(member.name)) == HWC:
                    with hwc.open('rb') as binary:
                        member.size = os.fstat(binary.fileno()).st_size
                        tar.addfile(member, binary)
                    replaced += 1
                else:
                    data = src.extractfile(member) if member.isfile() else None
                    try:
                        tar.addfile(member, data)
                    finally:
                        if data:
                            data.close()
            if replaced != 1:
                raise RuntimeError('Expected exactly one HWC in base archive')


def repack(archives, hwc, output):
    if nvidia._elf_class(hwc) != 64:
        raise RuntimeError('HWC must be an x86_64 ELF')
    output.mkdir(parents=True, exist_ok=True)
    for component, expected in BASE_HASHES.items():
        source = archives / f'waydroid-nvidia-{component}-{BASE_VERSION}.tar.gz'
        if digest(source) != expected:
            raise RuntimeError('Base archive mismatch: ' + source.name)
        name = f'waydroid-nvidia-{component}-{nvidia.UPSTREAM_VERSION}.tar.gz'
        dest = output / name
        if dest.exists():
            raise FileExistsError('Archive already exists: ' + name)
        # Validate in a private temporary directory; publish only complete,
        # verified archives. link() also refuses concurrent overwrites.
        with tempfile.TemporaryDirectory(prefix='.repack-', dir=output) as directory:
            staged = Path(directory) / name
            with staged.open('xb') as stream:
                if component == 'guest-android-x86_64':
                    replace_hwc(source, hwc, stream, name)
                else:
                    with source.open('rb') as src:
                        shutil.copyfileobj(src, stream)
            if digest(staged) != nvidia.ARCHIVES[component]:
                raise RuntimeError('Rebuilt archive differs from the pin: ' + name)
            os.link(staged, dest)
        print('Verified:', name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives', type=Path)
    parser.add_argument('hwc', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    repack(args.archives, args.hwc, args.output)
