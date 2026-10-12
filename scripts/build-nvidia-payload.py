#!/usr/bin/env python3
"""Prepare and verify the NVIDIA payload bundled by native package builders."""
import argparse
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/install-nvidia-payload.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
nvidia = installer.nvidia


def verify(root):
    report = nvidia.validate_payload(root)
    if not report['ok']: raise RuntimeError('; '.join(report['errors']))
    manifest = json.loads((root / 'manifest.json').read_text())
    if (manifest.get('archives') != nvidia.ARCHIVES or manifest.get('repo') != nvidia.UPSTREAM_REPO
            or manifest.get('version') != nvidia.UPSTREAM_VERSION
            or manifest.get('source') != nvidia.FORK_SOURCE):
        raise RuntimeError('Unexpected NVIDIA build provenance')
    for relative in report['checked']:
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != manifest['files'].get(relative):
            raise RuntimeError('NVIDIA build payload changed: ' + relative)


def main(stage_parent=None):
    destination = ROOT / 'target' / nvidia.PAYLOAD_ROOT.name
    if destination.exists():
        verify(destination)
    else:
        cache = ROOT / 'target/tools/nvidia'
        cache.mkdir(parents=True, exist_ok=True)
        installer.download_archives(cache)
        installer.stage(cache, destination)
        verify(destination)
    notices = destination / 'notices'
    notices.mkdir(exist_ok=True)
    for path in (ROOT / 'packaging/nvidia').iterdir():
        if path.is_file():
            (notices / path.name).write_bytes(path.read_bytes())
            (notices / path.name).chmod(0o644)
    if stage_parent is not None:
        staged = stage_parent / destination.name
        shutil.copytree(destination, staged)
        verify(staged)
    print('Bundled NVIDIA payload verified:', destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-parent', type=Path, help='Copy the verified payload into this package directory')
    main(parser.parse_args().stage_parent)
