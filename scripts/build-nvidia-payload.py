#!/usr/bin/env python3
"""Prepare and verify the NVIDIA payload bundled by native package builders."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/install-nvidia-payload.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
nvidia = installer.nvidia


def verify(root):
    report = nvidia.validate_payload(root)
    if not report['ok']: raise RuntimeError('; '.join(report['errors']))
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('archives') != nvidia.ARCHIVES: raise RuntimeError('Unexpected NVIDIA release')
    for relative in report['checked']:
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != manifest['files'].get(relative):
            raise RuntimeError('NVIDIA build payload changed: ' + relative)


def main():
    destination = ROOT / 'target' / nvidia.PAYLOAD_ROOT.name
    if destination.exists():
        verify(destination)
    else:
        cache = ROOT / 'target/tools/nvidia'
        cache.mkdir(parents=True, exist_ok=True)
        for component, checksum in nvidia.ARCHIVES.items():
            name = f'waydroid-nvidia-{component}-{nvidia.UPSTREAM_VERSION}.tar.gz'
            archive = cache / name
            if archive.exists():
                with archive.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() == checksum: continue
            with tempfile.TemporaryDirectory(prefix='.download-', dir=cache) as directory:
                downloaded = Path(directory) / name
                url = f'https://github.com/{nvidia.UPSTREAM_REPO}/releases/download/{nvidia.UPSTREAM_VERSION}/{name}'
                subprocess.run(['curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
                                '--retry', '2', '--max-time', '300', '--output', str(downloaded), url],
                               check=True, timeout=920)
                with downloaded.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                        raise RuntimeError('NVIDIA archive checksum mismatch: ' + name)
                downloaded.replace(archive)
        installer.stage(cache, destination)
        verify(destination)
    notices = destination / 'notices'
    notices.mkdir(exist_ok=True)
    for path in (ROOT / 'packaging/nvidia').iterdir():
        if path.is_file():
            (notices / path.name).write_bytes(path.read_bytes())
            (notices / path.name).chmod(0o644)
    print('Bundled NVIDIA payload verified:', destination)


if __name__ == '__main__': main()
