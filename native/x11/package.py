#!/usr/bin/env python3
"""Stage a content-addressed experimental X11 payload; never install or enable it."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FILES = {
    'anvildroid-x11-host': 'target/x11-host/anvildroid-x11-host',
    'runtime-x11.py': 'services/runtime-x11.py',
    'source-manifest.json': 'target/x11-host/source-manifest.json',
}


def stage():
    data = {name: (ROOT / path).read_bytes() for name, path in FILES.items()}
    manifest = dict(format=3, backend='native-x11', experimental=True, android_sdks=[33, 34, 35, 36],
        surface_protocol=1,
        capabilities=['app-windows', 'shared-bridge', 'wayland-surface-tree'],
        files={name: hashlib.sha256(content).hexdigest() for name, content in sorted(data.items())})
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    digest = hashlib.sha256(encoded).hexdigest()
    parent = ROOT / 'target/native-x11'
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / digest
    if destination.exists():
        if destination.is_symlink() or any((destination / name).is_symlink() for name in (*data, 'manifest.json')):
            raise RuntimeError('X11 staged payload contains substituted symlinks')
        if (destination / 'manifest.json').read_bytes() != encoded or any(
                (destination / name).read_bytes() != content for name, content in data.items()):
            raise RuntimeError('Existing content-addressed X11 payload was modified')
        for name in (*data, 'manifest.json'):
            (destination / name).chmod(0o755 if name in ('anvildroid-x11-host',) else 0o644)
        return destination
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=parent) as temporary:
        work = Path(temporary) / 'payload'
        work.mkdir(mode=0o755)
        for name, content in data.items():
            path = work / name
            path.write_bytes(content)
            path.chmod(0o755 if name in ('anvildroid-x11-host',) else 0o644)
        (work / 'manifest.json').write_bytes(encoded)
        (work / 'manifest.json').chmod(0o644)
        for source, name in (('target/x11-host/NOTICE', 'NOTICE'), ('native/menu-font-OFL.txt', 'menu-font-OFL.txt')):
            shutil.copyfile(ROOT / source, work / name)
        work.rename(destination)
    return destination


if __name__ == '__main__':
    print(stage())
