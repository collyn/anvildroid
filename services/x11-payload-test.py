#!/usr/bin/env python3
"""Reject unsafe/incompatible native X11 bundles before any component runs."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location('x11_payload', Path(__file__).with_name('runtime-x11.py'))
x11 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(x11)


def main():
    checks = []
    with tempfile.TemporaryDirectory(prefix='anvil-x11-payload-') as temporary:
        root = Path(temporary)
        manifest = dict(format=3, backend='native-x11', experimental=True, android_sdks=[33, 34, 35, 36],
            surface_protocol=1,
            capabilities=['app-windows', 'shared-bridge', 'wayland-surface-tree'], files={})
        for name in x11.PAYLOAD_FILES:
            p = root / name
            p.write_bytes(('fixture-' + name).encode())
            p.chmod(0o644)
            manifest['files'][name] = hashlib.sha256(p.read_bytes()).hexdigest()
        def write():
            (root/'manifest.json').write_text(json.dumps(manifest))
            (root/'manifest.json').chmod(0o644)
        def reject(label, owner=os.getuid()):
            try:
                x11.load_payload(root, trusted_owner=owner)
            except (RuntimeError, OSError):
                checks.append(label)
                return
            raise AssertionError('Admitted unsafe payload: ' + label)
        write()
        paths, digest = x11.load_payload(root, trusted_owner=os.getuid())
        assert set(paths) == x11.PAYLOAD_FILES and len(digest) == 64
        checks.append('verified-development-payload')
        reject('production-requires-root-owned-install', owner=0)
        file = root/'anvildroid-x11-host'
        original = file.read_bytes()
        file.write_bytes(b'changed')
        reject('modified-component')
        file.write_bytes(original)
        file.chmod(0o666)
        reject('writable-component')
        file.chmod(0o644)
        file.unlink();file.symlink_to('/dev/null')
        reject('symlink-component')
        file.unlink();os.mkfifo(file)
        reject('special-file-component')
        file.unlink();file.write_bytes(original)
        manifest['android_sdks'] = [33, 34, 35, 36, 37];write();reject('wrong-android-abi')
        manifest['android_sdks'] = [33, 34, 35, 36];manifest['surface_protocol'] = 2;write();reject('wrong-surface-protocol')
        manifest['surface_protocol'] = 1;manifest['files']['../../outside'] = '0'*64
        write();reject('unknown-or-traversal-component')
        del manifest['files']['../../outside'];write()
        (root/'manifest.json').unlink();(root/'manifest.json').symlink_to('/dev/null')
        reject('symlink-manifest')
    for name in checks:
        print('PASS:', name)
    print('PASS:', len(checks), 'native X11 payload checks')


if __name__ == '__main__':
    main()
