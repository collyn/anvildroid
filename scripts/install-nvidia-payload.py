#!/usr/bin/env python3
"""Stage checksum-pinned NVIDIA archives; never execute upstream installers.

--output stages a local tree for inspection. Default installation requires
root and publishes a new immutable controller payload. Fork CI inputs are
assembled by prepare-nvidia-fork.py; cached archives work without GitHub auth.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

base = Path(__file__).resolve().parents[1]
module = base / 'services/runtime-nvidia.py'
if not module.is_file(): module = base / 'runtime-nvidia.py'
spec = importlib.util.spec_from_file_location('nvidia', module)
nvidia = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nvidia)


def download_archives(archives):
    archives.mkdir(parents=True, exist_ok=True)
    missing = False
    for component, expected in nvidia.ARCHIVES.items():
        path = archives / f'waydroid-nvidia-{component}-{nvidia.UPSTREAM_VERSION}.tar.gz'
        if not path.exists():
            missing = True
            continue
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise RuntimeError('Cached NVIDIA archive checksum mismatch: ' + path.name)
    if not missing: return
    if getattr(nvidia, 'FORK_SOURCE', None):
        path = base / 'scripts/prepare-nvidia-fork.py'
        spec = importlib.util.spec_from_file_location('fork_payload', path)
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        inputs = archives.parent / f'nvidia-{nvidia.UPSTREAM_VERSION}-inputs'
        prepare.download(inputs)
        prepare.prepare(inputs, archives)
        return
    for component in nvidia.ARCHIVES:
        filename = f'waydroid-nvidia-{component}-{nvidia.UPSTREAM_VERSION}.tar.gz'
        url = f'https://github.com/{nvidia.UPSTREAM_REPO}/releases/download/{nvidia.UPSTREAM_VERSION}/{filename}'
        subprocess.run(['curl', '--fail', '--location', '--proto', '=https',
                        '--proto-redir', '=https', '--max-time', '300',
                        '--output', str(archives / filename), url], check=True, timeout=310)


def stage(archives, destination):
    destination = destination.absolute()
    if destination.exists(): raise RuntimeError('Destination already exists; refusing to overwrite active payload')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.nvidia-', dir=destination.parent) as temporary:
        root = Path(temporary) / 'payload'
        root.mkdir(mode=0o755)
        for component, expected in nvidia.ARCHIVES.items():
            archive = archives / f'waydroid-nvidia-{component}-{nvidia.UPSTREAM_VERSION}.tar.gz'
            # Read and extract from the same fd; the cached download may be
            # user-owned, so never hash one pathname and later reopen it.
            with archive.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                if digest != expected: raise RuntimeError('Checksum mismatch: ' + archive.name)
                stream.seek(0)
                with tarfile.open(fileobj=stream, mode='r:gz') as tar:
                    members = tar.getmembers()
                    errors = nvidia.validate_archive_members(members)
                    if errors: raise RuntimeError('; '.join(errors))
                    allowed = set(nvidia.PAYLOAD_FILES['host'] if component == 'host-x86_64' else
                                  sum((nvidia.PAYLOAD_FILES[g] for g in ('guest32', 'guest64', 'surfaceflinger')), ()))
                    for member in members:
                        relative = str(Path(member.name))
                        notice = relative in ('README.txt', 'SHA256SUMS.prebuilts', 'LICENSE', 'COPYING', 'NOTICE')
                        if not member.isfile() or (relative not in allowed and not notice): continue
                        if member.size > 256 * 1024**2: raise RuntimeError('Oversized NVIDIA library')
                        base = root if component == 'host-x86_64' else root / 'guest'
                        target = root / 'notices' / component / relative if notice else base / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(member) as source, target.open('xb') as output:
                            shutil.copyfileobj(source, output)
                        target.chmod(0o755 if component == 'host-x86_64' or relative == 'system/bin/surfaceflinger' else 0o644)
        notices = base / 'packaging/nvidia'
        if notices.is_dir():
            target = root / 'notices'
            target.mkdir(exist_ok=True)
            for path in notices.iterdir():
                if path.is_file():
                    (target / path.name).write_bytes(path.read_bytes())
                    (target / path.name).chmod(0o644)
        report = nvidia.validate_payload(root)
        if not report['ok']: raise RuntimeError('; '.join(report['errors']))
        manifest = {'repo': nvidia.UPSTREAM_REPO, 'version': nvidia.UPSTREAM_VERSION,
                    'archives': nvidia.ARCHIVES,
                    'files': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in report['checked']}}
        if getattr(nvidia, 'FORK_SOURCE', None): manifest['source'] = nvidia.FORK_SOURCE
        (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (root / 'manifest.json').chmod(0o644)
        for directory in root.rglob('*'):
            if directory.is_dir(): directory.chmod(0o755)
        os.rename(root, destination)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives', type=Path, nargs='?', help='Directory containing the three pinned archives')
    parser.add_argument('--download', action='store_true', help='Download pinned assets into a temporary directory')
    parser.add_argument('--output', type=Path, default=nvidia.PAYLOAD_ROOT)
    args = parser.parse_args()
    if args.output == nvidia.PAYLOAD_ROOT and os.geteuid() != 0:
        parser.error('Root is required for controller payload installation')
    if bool(args.archives) == args.download:
        parser.error('Provide an archive directory or --download')
    if args.download:
        with tempfile.TemporaryDirectory(prefix='anvil-nvidia-download-') as directory:
            archives = Path(directory)
            download_archives(archives)
            report = stage(archives, args.output)
    else:
        report = stage(args.archives, args.output)
    print(f"Validated {len(report['files'])} ELF payloads: {args.output}")
