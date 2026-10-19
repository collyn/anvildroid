#!/usr/bin/python3
"""Install the headless controller from this checkout, then enable its service.
Does not create or start an Android runtime. Existing workers keep running.
"""
import os
import argparse
import hashlib
from pathlib import Path
import stat
import socket
import struct
import time
import json
import io
import zipfile
import subprocess
import tempfile
import importlib.util

ROOT = Path(__file__).resolve().parent.parent
MODULES = ('runtime-system.py', 'runtime-controller.py', 'runtime-existing.py', 'runtime-keymap.py', 'runtime-host.py', 'runtime-images.py', 'runtime-android.py',
           'runtime-worker.py', 'runtime-x11.py', 'runtime-path-guard.py', 'runtime-linux.py', 'runtime-image-format.py', 'runtime-adb.py', 'runtime-network.py', 'runtime-storage.py', 'runtime-labels.py', 'runtime-transfer.py', 'runtime-catalog.py', 'runtime-download.py', 'runtime-extract.py', 'runtime-provision.py', 'runtime-arm.py', 'runtime-arm-source.py', 'runtime-gpu.py', 'runtime-nvidia.py', 'runtime-resources.py', 'runtime-desktop.py', 'runtime-workarea.py', 'runtime-features.py', 'runtime-compatibility.py', 'runtime-shares.py', 'runtime-default-shares.py', 'runtime-device.py', 'runtime-components.py', 'runtime-diagnostics.py')
DESTINATION = Path('/usr/local/lib/anvildroid-controller')
SOCKET_PATH = Path('/run/anvildroid/control.sock')


def trusted_directory(path, strict=True):
    # Strict mode (the destination itself): root-owned, not a symlink, no
    # group/world write bits. Pre-existing system ancestors only need to be
    # real directories, never symlinks: the installer writes as root and uses
    # atomic replace() that never follows symlinks, so neither ancestor
    # ownership nor ancestor write bits change where files actually land
    # (some hosts legitimately have e.g. a user-owned /usr).
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeError(f'Unsafe installation directory (symlink): {path}')
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError(f'Unsafe installation directory (not a directory): {path}')
        if strict:
            if info.st_uid != 0:
                raise RuntimeError(f'Unsafe installation directory (not root-owned): {path}')
            if info.st_mode & 0o022:
                raise RuntimeError(f'Unsafe installation directory (group/world-writable): {path}')
    else:
        path.mkdir(mode=0o755)
    if path != path.parent:
        trusted_directory(path.parent, strict=False)


def replace(path, data, mode=0o644):
    # Atomic file replacement never follows a pre-existing destination symlink.
    fd, temporary = tempfile.mkstemp(prefix='.install-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_controller_files(source_root, destination=DESTINATION, arm_archive=None, x11_payload=None):
    """Validate the payload of source_root (a repo checkout or AppImage stage
    mirroring the portable release layout) and install it under destination.
    Purely file operations; does not touch systemd."""
    # Capture and compile all source before writing anything.
    sources = {name: (source_root / 'services' / name).read_bytes() for name in MODULES}
    for name, data in sources.items():
        compile(data, name, 'exec')
    x11_sources = None
    # Portable bundles carry their verified native backend beside services/.
    # Explicit --x11-payload remains available for checkout/development installs.
    bundled_x11 = source_root / 'native-x11'
    if x11_payload is None and (bundled_x11.exists() or bundled_x11.is_symlink()):
        x11_payload = bundled_x11
    if x11_payload is not None:
        spec = importlib.util.spec_from_file_location('install_x11', source_root / 'services/runtime-x11.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        paths, _ = module.load_payload(x11_payload, trusted_owner=x11_payload.stat().st_uid)
        x11_sources = {name: path.read_bytes() for name, path in paths.items()}
        if x11_sources['runtime-x11.py'] != sources['runtime-x11.py']:
            raise RuntimeError('Native X11 payload/controller source mismatch')
        if json.loads(x11_sources['source-manifest.json']).get('sanitized') is not False:
            raise RuntimeError('Do not install sanitizer/test native X11 builds')
        x11_sources['manifest.json'] = (x11_payload / 'manifest.json').read_bytes()
        for name in ('NOTICE', 'menu-font-OFL.txt'):
            x11_sources[name] = (x11_payload / name).read_bytes()
    bridge = (source_root / 'target/native/libanvildroid-runtime-window.so').read_bytes()
    if bridge[:4] != b'\x7fELF' or bridge[4:6] != b'\x02\x01' or bridge[18:20] != b'\x3e\x00':
        raise RuntimeError('Build the x86_64 Android desktop bridge with scripts/build-runtime-desktop.sh first')
    path_guard = (source_root / 'target/native/libanvildroid-path-guard.so').read_bytes()
    if path_guard[:6] != b'\x7fELF\x02\x01' or path_guard[18:20] != b'\x3e\x00':
        raise RuntimeError('Build the Android pathname compatibility helper first')
    ime = (source_root / 'target/ime/AnvilDroidIme.apk').read_bytes()
    if ime[:4] != b'PK\x03\x04': raise RuntimeError('Build the desktop IME APK first')
    shutdown = (source_root / 'target/shutdown/anvildroid-shutdown.jar').read_bytes()
    with zipfile.ZipFile(io.BytesIO(shutdown)) as archive:
        if archive.read('classes.dex')[:4] != b'dex\n': raise RuntimeError('Build the Android shutdown helper first')
    assets = {str(path.relative_to(source_root / 'native')): path.read_bytes() for path in (source_root / 'native/overlay').rglob('*') if path.is_file()}
    for name in ('anvildroid-apps.rc', 'anvildroid-tasks.rc', 'anvildroid-apps.sh', 'anvildroid-tasks.sh', 'anvildroid-keymap.rc', 'anvildroid-keymap.sh'):
        assets[name] = (source_root / 'native' / name).read_bytes()
    assets['menu-font-OFL.txt'] = (source_root / 'native/menu-font-OFL.txt').read_bytes()
    assets['overlay-key.p12'] = (source_root / 'target/native/overlay-key.p12').read_bytes()
    policy_spec = importlib.util.spec_from_file_location(
        'install_runtime_apparmor', source_root / 'scripts/install-runtime-apparmor.py')
    policy = importlib.util.module_from_spec(policy_spec)
    policy_spec.loader.exec_module(policy)
    print(policy.install())
    trusted_directory(destination.parent, strict=False)
    trusted_directory(destination)
    for name, data in sources.items():
        replace(destination / name, data)
    if x11_sources is not None:
        native = destination / 'native-x11'
        trusted_directory(native)
        for name, data in x11_sources.items():
            if name == 'manifest.json': continue
            replace(native / name, data, 0o755 if name in ('anvildroid-x11-host',) else 0o644)
        replace(native / 'manifest.json', x11_sources['manifest.json'])
    if arm_archive is not None: replace(destination / 'libndk-0.2.3.zip', arm_archive)
    replace(destination / 'scrcpy-server-cancel.jar', (source_root / 'target/input-diagnostic/scrcpy-server-cancel.jar').read_bytes())
    replace(destination / 'libanvildroid-window.so', bridge)
    replace(destination / 'libanvildroid-path-guard.so', path_guard)
    replace(destination / 'AnvilDroidIme.apk', ime)
    replace(destination / 'anvildroid-shutdown.jar', shutdown)
    for name, data in assets.items():
        target = destination / 'provision' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        trusted_directory(target.parent)
        replace(target, data)



def systemd_payload(source_root):
    return {relative: (source_root / 'packaging/systemd' / name).read_bytes()
            for relative, name in (
                ('anvildroid-controller.service', 'anvildroid-controller.service'),
                ('waydroid-container.service.d/50-anvildroid.conf', '50-anvildroid.conf'))}


def install_systemd_files(payload, directory=Path('/etc/systemd/system')):
    """Install the controller unit and Default hook without running systemd."""
    trusted_directory(directory)
    for relative, data in payload.items():
        target = directory / relative
        trusted_directory(target.parent)
        replace(target, data)


def wait_for_ready_socket(socket_path=SOCKET_PATH, deadline=None, error_hint='inspect systemd service logs'):
    """Poll socket_path until it serves an authenticated 'requirements' response.
    Type=simple (or a detached spawn) becomes alive before Python binds the
    socket, so the first GUI/client request must not race startup."""
    if deadline is None:
        deadline = time.monotonic() + 30
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(str(socket_path))
                _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != 0:
                    raise RuntimeError('Untrusted controller socket')
                connection.sendall(b'{"op":"requirements"}\n')
                response = bytearray()
                while not response.endswith(b'\n') and len(response) <= 65536:
                    part = connection.recv(4096)
                    if not part: raise OSError('Controller disconnected during readiness check')
                    response.extend(part)
                if not json.loads(response).get('ok'):
                    raise RuntimeError('Controller readiness request failed')
            return
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                raise RuntimeError(f'Controller socket did not become ready; {error_hint}')
            time.sleep(.1)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run this installer as root.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--x11-payload', type=Path)
    parser.add_argument('--arm-archive', type=Path)
    args = parser.parse_args()
    arm_archive = None
    if args.arm_archive:
        with args.arm_archive.open('rb') as stream: arm_archive = stream.read(64 * 1024**2 + 1)
        if hashlib.sha256(arm_archive).hexdigest() != 'a142d1586c9eafb5edf62277110f2128bc03066179a6783bcaa33ee322e1cbd0':
            raise RuntimeError('ARM archive checksum mismatch')
    units = systemd_payload(ROOT)
    trusted_directory(Path('/etc/systemd/system'))
    # Controller is stopped during updates, so imports cannot mix releases.
    loaded = subprocess.run(['systemctl', 'show', '--property=LoadState', '--value',
                             'anvildroid-controller.service'], check=True, capture_output=True, text=True)
    if loaded.stdout.strip() != 'not-found':
        subprocess.run(['systemctl', 'stop', 'anvildroid-controller.service'], check=True)
    install_controller_files(ROOT, arm_archive=arm_archive, x11_payload=args.x11_payload)
    install_systemd_files(units)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'anvildroid-controller.service'], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', 'anvildroid-controller.service'], check=True)
    wait_for_ready_socket()
    print('Controller installed and socket ready. No Android runtime was created or started.')
    print('Default integration takes effect on the next Waydroid container service restart.')


if __name__ == '__main__':
    main()
