#!/usr/bin/env python3
"""Build the native per-app adapter; never start a display or Android runtime."""
import os
from pathlib import Path
import shlex
import subprocess
import hashlib
import json
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = Path(os.environ.get('ANVILDROID_X11_BUILD_DIR', ROOT / 'target/x11-host')).resolve()
PROTOCOLS = {
    'xdg-shell': 'stable/xdg-shell/xdg-shell.xml',
    'viewporter': 'stable/viewporter/viewporter.xml',
    'linux-dmabuf': 'unstable/linux-dmabuf/linux-dmabuf-unstable-v1.xml',
    'text-input': 'unstable/text-input/text-input-unstable-v3.xml',
    'xdg-activation': 'staging/xdg-activation/xdg-activation-v1.xml',
    'pointer-constraints': 'unstable/pointer-constraints/pointer-constraints-unstable-v1.xml',
    'relative-pointer': 'unstable/relative-pointer/relative-pointer-unstable-v1.xml',
}


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    root = Path(subprocess.check_output(['pkg-config', '--variable=pkgdatadir', 'wayland-protocols'], text=True).strip())
    cc = shlex.split(os.environ.get('CC', 'cc'))
    cxx = shlex.split(os.environ.get('CXX', 'c++'))
    objects = []
    protocol_hashes = {}
    notices = [(HERE / 'NOTICE').read_text()]
    for name, relative in PROTOCOLS.items():
        xml = root / relative
        protocol_hashes[relative] = hashlib.sha256(xml.read_bytes()).hexdigest()
        notices.append(ET.parse(xml).getroot().findtext('copyright', default=''))
        for kind in ('server-header', 'client-header', 'private-code'):
            output = OUT / (name + ('-server.h' if kind == 'server-header' else '-client.h' if kind == 'client-header' else '.c'))
            subprocess.run(['wayland-scanner', kind, str(xml), str(output)], check=True)
        obj = OUT / (name + '.o')
        subprocess.run(cc + ['-O2', '-c', str(OUT / (name + '.c')), '-o', str(obj)], check=True)
        objects.append(str(obj))
    packages = ['wayland-server', 'egl', 'glesv2', 'x11', 'xcursor', 'xkbcommon', 'xi', 'xfixes', 'xext']
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', *packages], text=True))
    libs = shlex.split(subprocess.check_output(['pkg-config', '--libs', *packages], text=True))
    checked = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('ANVILDROID_X11_SANITIZE') == '1' else []
    subprocess.run(cxx + ['-std=c++17', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                         *checked, '-I' + str(OUT), *flags, str(HERE / 'wayland-host.cpp'),
                         *objects, '-o', str(OUT / 'anvildroid-x11-host'), *libs], check=True)
    subprocess.run(cxx + ['-std=c++17', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                         *checked, str(HERE / 'surface-tree-check.cpp'), '-o', str(OUT / 'surface-tree-check'), '-lX11'], check=True)
    subprocess.run(cxx + ['-std=c++17', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                         *checked, '-I' + str(OUT), str(HERE / 'host-client-check.cpp'), *objects,
                         '-o', str(OUT / 'host-client-check'), '-lwayland-client', '-lX11'], check=True)
    subprocess.run(cxx + ['-std=c++17', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                         *checked, '-I' + str(OUT), *flags, str(HERE / 'feature-state-check.cpp'),
                         *objects, '-o', str(OUT / 'feature-state-check'), *libs, '-lXtst'], check=True)
    subprocess.run(cxx + ['-std=c++17', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                         *checked, '-I' + str(OUT), str(HERE / 'feature-client-check.cpp'), *objects,
                         '-o', str(OUT / 'feature-client-check'), '-lwayland-client', '-lX11', '-lXtst'], check=True)
    mapper = OUT / 'keymap-x11-sink.o'
    subprocess.run(cc + ['-O0', '-g', '-Wall', '-Wextra', '-Werror', *checked,
                         '-c', str(ROOT / 'tests/keymap-x11-sink.c'), '-o', str(mapper)], check=True)
    text_state = OUT / 'keymap-text-state.o'
    subprocess.run(cc + ['-O2', '-g', *checked, '-c', str(ROOT / 'native/ime/text-state.c'), '-o', str(text_state)], check=True)
    subprocess.run(cxx + ['-std=c++17', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                         *checked, '-I' + str(OUT), str(HERE / 'keymap-client-check.cpp'), *objects,
                         str(mapper), str(text_state),
                         '-o', str(OUT / 'keymap-client-check'), '-lwayland-client', '-lX11', '-lXtst', '-ldl', '-pthread'], check=True)
    print(OUT / 'anvildroid-x11-host')
    (OUT / 'NOTICE').write_text('\n\n'.join(notices) + '\n')
    (OUT / 'source-manifest.json').write_text(json.dumps(dict(
        sources={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(HERE.iterdir()) if p.suffix in ('.cpp', '.hpp', '.py')},
        protocols=protocol_hashes,
        wayland_protocols_version=subprocess.check_output(['pkg-config', '--modversion', 'wayland-protocols'], text=True).strip(),
        sanitized=bool(checked)), sort_keys=True, indent=2)+'\n')


if __name__ == '__main__':
    build()
