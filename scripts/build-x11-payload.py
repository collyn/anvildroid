#!/usr/bin/env python3
"""Build or stage the verified native X11 payload used by all Linux packages."""
import argparse
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'target/native-x11/package'


def verify(directory):
    module = runpy.run_path(str(ROOT / 'services/runtime-x11.py'))
    paths, digest = module['load_payload'](directory, trusted_owner=directory.stat().st_uid)
    if paths['runtime-x11.py'].read_bytes() != (ROOT / 'services/runtime-x11.py').read_bytes():
        raise RuntimeError('Native X11 payload/controller source mismatch; rebuild the payload')
    sources = json.loads(paths['source-manifest.json'].read_text())
    if sources.get('sanitized') is not False:
        raise RuntimeError('Do not package sanitizer/test builds')
    expected = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT / 'native/x11').iterdir() if p.suffix in ('.cpp', '.hpp', '.py')}
    if sources.get('sources') != expected:
        raise RuntimeError('Native X11 source mismatch; rebuild the payload')
    for name in ('NOTICE', 'menu-font-OFL.txt'):
        if not (directory / name).is_file() or (directory / name).is_symlink():
            raise RuntimeError('Missing native X11 notice: ' + name)
    return digest


def copy(source, destination):
    verify(source)
    if source.resolve() != destination.resolve():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    for path in destination.rglob('*'):
        path.chmod(0o755 if path.is_dir() or path.name == 'anvildroid-x11-host' else 0o644)
    destination.chmod(0o755)
    verify(destination)


def build():
    supplied = os.environ.get('ANVILDROID_X11_PAYLOAD')
    if supplied:
        source = Path(supplied).resolve()
    else:
        subprocess.run(['python3', str(ROOT / 'native/x11/build-host.py')], cwd=ROOT, check=True)
        source = runpy.run_path(str(ROOT / 'native/x11/package.py'))['stage']()
    copy(source, PACKAGE)
    print('Native X11 package payload verified:', verify(PACKAGE))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-parent', type=Path)
    args = parser.parse_args()
    if args.stage_parent:
        copy(PACKAGE, args.stage_parent / 'native-x11')
    else:
        build()
