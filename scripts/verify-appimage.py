#!/usr/bin/env python3
"""Validate an extracted AppImage payload and its optional glibc baseline."""
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import zipfile

REQUIRED = (
    'AppRun', 'AppRun.wrapped', 'usr/bin/anvildroid-gui', 'usr/bin/anvildroid',
    'scripts/bootstrap-appimage.py', 'scripts/start-runtime-controller.py', 'scripts/setup-waydroid.py',
    'scripts/install-runtime-controller.py', 'services/runtime-host.py',
    'target/native/libanvildroid-path-guard.so', 'target/native/libanvildroid-runtime-window.so', 'target/native/libanvildroid-window.so',
    'target/native/overlay-key.p12', 'target/ime/AnvilDroidIme.apk',
    'target/shutdown/anvildroid-shutdown.jar', 'native/overlay/AndroidManifest.xml',
    'native/anvildroid-apps.rc', 'native/anvildroid-apps.sh',
    'native/anvildroid-tasks.rc', 'native/anvildroid-tasks.sh',
    'usr/lib/gstreamer-1.0/libgstapp.so',
    'usr/lib/gstreamer-1.0/libgstcoreelements.so', 'usr/libexec/gst-plugin-scanner',
)

# Keep glibc and the platform display/driver ABI on the host. Everything else
# referenced by the bundled GTK/WebKit ELF objects must travel with the image.
HOST_LIBRARIES = {
    'ld-linux-x86-64.so.2', 'libc.so.6', 'libm.so.6', 'libresolv.so.2',
    'libdl.so.2', 'libpthread.so.0', 'librt.so.1', 'libgcc_s.so.1',
    'libEGL.so.1', 'libGL.so.1', 'libGLdispatch.so.0', 'libGLX.so.0',
    'libX11.so.6', 'libX11-xcb.so.1', 'libxcb.so.1',
    'libdrm.so.2', 'libgbm.so.1', 'libwayland-client.so.0',
}


def validate(root, glibc_max=None):
    for name in REQUIRED:
        path = root / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError('AppImage missing payload: ' + name)
    installer = ast.parse((root / 'scripts/install-runtime-controller.py').read_text())
    modules = next(ast.literal_eval(node.value) for node in installer.body
                   if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MODULES' for t in node.targets))
    for module in modules:
        path = root / 'services' / module
        if not path.is_file():
            raise RuntimeError('AppImage missing controller module: ' + module)
        compile(path.read_bytes(), module, 'exec')
    for path in (root / 'scripts').glob('*.py'):
        compile(path.read_bytes(), str(path), 'exec')
    bridge = (root / 'target/native/libanvildroid-window.so').read_bytes()
    if bridge != (root / 'target/native/libanvildroid-runtime-window.so').read_bytes():
        raise RuntimeError('Native bridges differ')
    if bridge[:6] != b'\x7fELF\x02\x01' or bridge[18:20] != b'\x3e\x00':
        raise RuntimeError('Native bridge is not x86_64 ELF')
    for name in ('target/ime/AnvilDroidIme.apk', 'target/shutdown/anvildroid-shutdown.jar'):
        with zipfile.ZipFile(root / name) as archive:
            if archive.testzip() is not None or archive.read('classes.dex')[:4] != b'dex\n':
                raise RuntimeError('Invalid Android helper: ' + name)
    observed = (0, 0)
    seen = set()
    bundled_names = {p.name for p in (root / 'usr/lib').rglob('*') if p.is_file()}
    for base in ('usr/bin', 'usr/lib', 'usr/libexec'):
        for path in (root / base).rglob('*'):
            if not path.is_file() or path.resolve() in seen:
                continue
            seen.add(path.resolve())
            with path.open('rb') as stream:
                if stream.read(4) != b'\x7fELF':
                    continue
            dynamic = subprocess.check_output(['readelf', '--dynamic', str(path)], text=True, timeout=15)
            needed = set(re.findall(r'\(NEEDED\).*\[(.*?)\]', dynamic))
            missing = needed - bundled_names - HOST_LIBRARIES
            if missing:
                raise RuntimeError(f'{path.relative_to(root)} has unbundled dependencies: {sorted(missing)}')
            output = subprocess.check_output(['readelf', '--version-info', str(path)], text=True, timeout=15)
            versions = [tuple(map(int, m.split('.'))) for m in re.findall(r'\bGLIBC_(\d+\.\d+(?:\.\d+)?)\b', output)]
            required = max(versions, default=(0, 0))
            observed = max(observed, required)
            if glibc_max and required > glibc_max:
                raise RuntimeError(f'{path.relative_to(root)} requires glibc {".".join(map(str, required))}, above requested baseline')
    result = subprocess.run([str(root / 'AppRun.wrapped'), '--cli', '--help'],
                            capture_output=True, text=True, timeout=20)
    if result.returncode or 'AnvilDroid' not in result.stdout:
        raise RuntimeError('Bundled CLI failed to start: ' + result.stderr[-1000:])
    return {'payload': 'verified', 'maximum_glibc': '.'.join(map(str, observed)), 'cli': 'passed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('appdir', type=Path)
    parser.add_argument('--glibc-max', help='e.g. 2.39 for Ubuntu 24.04')
    args = parser.parse_args()
    print(json.dumps(validate(args.appdir.resolve(), tuple(map(int, args.glibc_max.split('.'))) if args.glibc_max else None)))
