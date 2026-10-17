#!/usr/bin/env python3
"""Verify built package payloads and smoke-test installed GUI/CLI binaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import socket
import stat
import subprocess
import tempfile
import time
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = Path('usr/local/lib/anvildroid-controller')


def controller_environment(root):
    service = (root / 'usr/lib/systemd/system/anvildroid-controller.service').read_text()
    path = re.search(r'^Environment=PATH=(.+)$', service, re.MULTILINE)[1]
    # Exercise the same PATH as systemd, with private tools relocated for extraction.
    private = '/usr/local/lib/anvildroid-controller/tools'
    assert path.startswith(private + ':'), 'Controller cannot find packaged image tools'
    return dict(os.environ, PATH=path.replace(private, str(root / PAYLOAD / 'tools'), 1))


def image_tools(root):
    environment = controller_environment(root)
    tools = root / PAYLOAD / 'tools'
    assert (tools / 'NOTICE.txt').is_file() and (tools / 'source.properties').is_file()
    for command in ('aapt', 'apksigner', 'unshare', 'java'):
        run(['/bin/sh', '-c', 'command -v "$1"', 'verify-image-tool', command], env=environment)
    run(['aapt', 'version'], env=environment)
    run(['apksigner', 'version'], env=environment)
    with tempfile.TemporaryDirectory(prefix='anvil-image-tools-') as directory:
        work = Path(directory)
        manifest = work / 'AndroidManifest.xml'
        manifest.write_text('<manifest package="org.anvildroid.packageprobe"><application/></manifest>')
        resources = work / 'res/layout'
        resources.mkdir(parents=True)
        (resources / 'caption.xml').write_text('<FrameLayout/>')
        unsigned, signed = work / 'unsigned.apk', work / 'signed.apk'
        run(['aapt', 'package', '-f', '-M', str(manifest), '-S', str(work / 'res'), '-F', str(unsigned)], env=environment)
        run(['apksigner', 'sign', '--min-sdk-version', '23', '--ks',
             str(root / PAYLOAD / 'provision/overlay-key.p12'), '--ks-pass', 'pass:anvildroid',
             '--out', str(signed), str(unsigned)], env=environment)
        run(['apksigner', 'verify', '--min-sdk-version', '23', str(signed)], env=environment)
        assert 'org.anvildroid.packageprobe' in run(['aapt', 'dump', 'badging', str(signed)], env=environment).stdout
    print('Controller PATH, packaged aapt/apksigner, Java, overlay compilation/signature: OK')


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=120, **kwargs)


def binaries(root, version):
    cli = root / 'usr/bin/anvildroid'
    gui = root / 'usr/bin/anvildroid-gui'
    for binary in (cli, gui):
        assert binary.is_file() and os.access(binary, os.X_OK), f'Missing executable: {binary}'
        dependencies = run(['ldd', str(binary)])
        assert 'not found' not in dependencies.stdout, dependencies.stdout
        embedded = binary.read_bytes()
        assert b'Starting native X11 app-window adapter for Default runtime' in embedded, 'Default X11 launcher is stale'
        assert b'Starting nested Weston for Default runtime' not in embedded, 'Default X11 still launches Weston'
    with tempfile.TemporaryDirectory(prefix='anvil-cli-smoke-') as directory:
        fixture = Path(directory)
        environment = dict(os.environ, HOME=directory, XDG_CONFIG_HOME=str(fixture / 'config'),
                           XDG_DATA_HOME=str(fixture / 'data'), PATH=str(fixture))
        help_text = run([str(cli), '--help'], env=environment).stdout
        assert f'AnvilDroid {version} ' in help_text, help_text
        assert 'Usage: anvildroid' in help_text and '--runtime ID' in help_text
        marker = fixture / 'called'
        backend = fixture / 'waydroid'
        backend.write_text('#!/bin/sh\nprintf called > "$HOME/called"\nexit 7\n')
        backend.chmod(0o755)
        for arguments in (['--runtime', 'other', 'stop'], ['--runtime=other', 'apps'],
                          ['--runtime'], ['unknown-command']):
            result = subprocess.run([str(cli), *arguments], env=environment,
                                    text=True, capture_output=True, timeout=10)
            assert result.returncode != 0 and not marker.exists(), result
        result = subprocess.run([str(cli), 'status'], env=environment,
                                text=True, capture_output=True, timeout=10)
        assert result.returncode == 7 and marker.read_text() == 'called', result
    print(f'GUI/CLI dependencies, CLI version {version}, routing and exit codes: OK')


def payload(root, installed=False):
    base = root / PAYLOAD
    for relative, expected in (('var', 0o755), ('var/lib', 0o755), ('run', 0o755),
                               ('var/lib/anvildroid-controller', 0o700),
                               ('run/anvildroid', 0o755)):
        path = root / relative
        actual = stat.S_IMODE(path.stat().st_mode)
        assert actual == expected, f'Wrong directory permissions: {path}: {actual:04o}, expected {expected:04o}'
    required = ('runtime-controller.py', 'runtime-worker.py', 'runtime-adb.py',
                'runtime-features.py', 'runtime-compatibility.py', 'runtime-shares.py', 'runtime-components.py', 'runtime-diagnostics.py',
                'runtime-path-guard.py', 'libanvildroid-window.so',
                'libanvildroid-hwc-shim.so', 'libanvildroid-android-window.so',
                'libanvildroid-path-guard.so', 'AnvilDroidIme.apk',
                'anvildroid-shutdown.jar', 'scrcpy-server-cancel.jar',
                'provision/overlay-key.p12', 'provision/system/etc/init/init.waydroid.rc',
                'provision/vendor/overlay/AnvilDroidCaption/AnvilDroidCaption.apk',
                'scripts/install-runtime-apparmor.py')
    for relative in required:
        path = base / relative
        assert path.is_file() and path.stat().st_size, f'Missing payload: {path}'
    for relative in ('AnvilDroidIme.apk', 'anvildroid-shutdown.jar', 'scrcpy-server-cancel.jar'):
        with zipfile.ZipFile(base / relative) as archive:
            assert 'classes.dex' in archive.namelist(), f'Missing Android bytecode: {relative}'
            assert archive.testzip() is None, f'Corrupt Android payload: {relative}'
    scrcpy = json.loads((base / 'scrcpy-build.json').read_text())
    assert scrcpy['revision'] == 'f01231dff8294fe2c99045a4f9a14b233a71bb86'
    assert hashlib.sha256((base / 'scrcpy-server-cancel.jar').read_bytes()).hexdigest() == scrcpy['jar_sha256']
    assert hashlib.sha256((base / 'scrcpy-LICENSE').read_bytes()).hexdigest() == scrcpy['license_sha256']
    native = base / 'native-x11'
    x11 = runpy.run_path(str(base / 'runtime-x11.py'))
    x11['load_payload'](native, trusted_owner=0 if installed else native.stat().st_uid)
    assert (native / 'runtime-x11.py').read_bytes() == (base / 'runtime-x11.py').read_bytes()
    for name in ('NOTICE', 'menu-font-OFL.txt'):
        assert (native / name).is_file(), f'Missing X11 notice: {name}'
    assert os.access(native / 'anvildroid-x11-host', os.X_OK), 'X11 adapter executable mode'
    dependencies = run(['ldd', str(native / 'anvildroid-x11-host')]).stdout
    assert 'not found' not in dependencies, dependencies
    print('Native X11 surface adapter manifest, hashes, dependencies and notices: OK')
    for path in base.rglob('*.py'):
        compile(path.read_bytes(), str(path), 'exec')
    manifests = list(base.glob('nvidia-*/manifest.json'))
    if installed:
        # Development hosts can retain older manually installed payloads. Verify
        # the exact version selected by the installed controller, without deleting them.
        nvidia = runpy.run_path(str(base / 'runtime-nvidia.py'))
        manifests = [base / nvidia['PAYLOAD_ROOT'].name / 'manifest.json']
    assert len(manifests) == 1 and manifests[0].is_file(), 'Expected one pinned NVIDIA payload'
    manifest = json.loads(manifests[0].read_text())
    for relative, digest in manifest['files'].items():
        path = manifests[0].parent / relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f'Payload changed: {path}'
    for relative in ('usr/lib/systemd/system/anvildroid-controller.service',
                     'usr/lib/systemd/system/waydroid-container.service.d/50-anvildroid.conf',
                     'usr/share/applications/org.anvildroid.gui.desktop',
                     'usr/share/licenses/anvildroid-controller/LICENSE-MIT'):
        assert (root / relative).is_file(), f'Missing integration/notice: {relative}'
    # Minimal distro images may intentionally skip /usr/share/doc on install.
    # Check documentation in the archive; runtime provenance remains beside the jar.
    if not installed:
        for name in ('menu-font-OFL.txt', 'scrcpy-LICENSE'):
            assert (root / 'usr/share/doc/anvildroid-controller' / name).is_file(), name
    if installed:
        assert base.stat().st_uid == 0, f'Controller directory is not root-owned: {base}'
    assert not base.stat().st_mode & 0o022, f'Writable controller directory: {base}'
    for path in base.rglob('*'):
        if installed:
            assert path.stat().st_uid == 0, f'Controller asset is not root-owned: {path}'
        assert not path.stat().st_mode & 0o022, f'Writable controller asset: {path}'
    print('Android bytecode, Python modules, NVIDIA hashes, service, desktop and notices: OK')


def gui_smoke(root):
    with tempfile.TemporaryDirectory(prefix='anvil-gui-smoke-') as directory:
        home = Path(directory)
        runtime = home / 'runtime'
        runtime.mkdir(mode=0o700)
        environment = dict(os.environ, HOME=directory, XDG_CONFIG_HOME=str(home / 'config'),
                           XDG_DATA_HOME=str(home / 'data'), XDG_CACHE_HOME=str(home / 'cache'),
                           XDG_RUNTIME_DIR=str(runtime), GDK_BACKEND='x11',
                           ANVILDROID_STARTUP_TRACE='1', LIBGL_ALWAYS_SOFTWARE='1')
        environment.pop('WAYLAND_DISPLAY', None)
        # Headless containers cannot provide WebKit's nested namespace sandbox.
        # This variable is confined to this test process and never packaged.
        environment['WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS'] = '1'
        with (home / 'gui.log').open('w+') as log:
            process = subprocess.Popen([str(root / 'usr/bin/anvildroid-gui')],
                                       env=environment, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    log.seek(0)
                    output = log.read()
                    assert process.poll() is None, f'GUI exited with {process.returncode}:\n{output}'
                    if 'frontend-first-frame' in output:
                        time.sleep(2)
                        log.seek(0)
                        output = log.read()
                        assert process.poll() is None and 'panicked at' not in output, output
                        print('Installed GUI reached its first frontend frame: OK')
                        return
                    time.sleep(0.25)
                raise AssertionError(f'GUI did not render its frontend:\n{output}')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def controller_smoke(root):
    with tempfile.TemporaryDirectory(prefix='anvil-controller-smoke-') as directory:
        temporary = Path(directory)
        endpoint = temporary / 'control.sock'
        (temporary / 'store').mkdir(mode=0o700)
        with (temporary / 'controller.log').open('w+') as log:
            process = subprocess.Popen([
                'python3', '-B', str(root / PAYLOAD / 'runtime-controller.py'),
                '--store', str(temporary / 'store'), '--socket', str(endpoint),
                '--allow-temporary-storage',
            ], stdout=log, stderr=log, env=controller_environment(root))
            try:
                deadline = time.monotonic() + 15
                while not endpoint.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.seek(0)
                        raise AssertionError(f'Controller exited: {log.read()}')
                    time.sleep(0.1)
                for operation in ('list', 'requirements'):
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                        connection.settimeout(10)
                        connection.connect(str(endpoint))
                        connection.sendall(json.dumps({'op': operation}).encode() + b'\n')
                        response = bytearray()
                        while not response.endswith(b'\n'):
                            part = connection.recv(65536)
                            assert part, 'Controller disconnected before responding'
                            response.extend(part)
                        data = json.loads(response)
                        assert data['ok'] and 'result' in data, data
                print('Installed controller starts and serves list/requirements over Unix IPC: OK')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def extract(package, destination, version):
    if package.name.endswith('.deb'):
        actual = run(['dpkg-deb', '-f', str(package), 'Version']).stdout.strip()
        assert actual == version.replace('-', '~', 1), actual
        assert run(['dpkg-deb', '-f', str(package), 'Architecture']).stdout.strip() == 'amd64'
        run(['dpkg-deb', '-x', str(package), str(destination)])
    elif package.name.endswith('.rpm'):
        actual = run(['rpm', '-qp', '--qf', '%{VERSION}', str(package)]).stdout
        assert actual == version.replace('-', '~', 1), actual
        assert run(['rpm', '-qp', '--qf', '%{ARCH}', str(package)]).stdout == 'x86_64'
        dependencies = run(['rpm', '-qp', '--requires', str(package)]).stdout
        assert not re.search(r'lib(c|dl|m)\.so\(|libc\+\+\.so|liblog\.so', dependencies), dependencies
        for library in ('libwayland-client.so.0()(64bit)', 'libwayland-server.so.0()(64bit)'):
            assert library in dependencies.splitlines(), f'Missing Wayland runtime dependency: {library}'
        assert 'wayland-libs' not in dependencies.splitlines(), 'Use Wayland ABI dependencies for Fedora'
        archive = subprocess.run(['rpm2cpio', str(package)], check=True, capture_output=True, timeout=120)
        subprocess.run(['cpio', '-idm', '--quiet', '--no-absolute-filenames'], cwd=destination,
                       input=archive.stdout, check=True, capture_output=True, timeout=120, umask=0o022)
    elif package.name.endswith('.pkg.tar.zst'):
        entries = run(['tar', '--use-compress-program=zstd', '-tf', str(package)]).stdout.splitlines()
        assert '.PKGINFO' in entries and '.INSTALL' in entries, 'pacman requires root metadata names without ./ prefixes'
        run(['tar', '--use-compress-program=zstd', '--same-permissions', '-xf', str(package), '-C', str(destination)])
        metadata = (destination / '.PKGINFO').read_text()
        assert f'pkgver = {version}-1\n' in metadata and 'arch = x86_64\n' in metadata
        assert int(re.search(r'^size = (\d+)$', metadata, re.MULTILINE)[1]) > 0
        assert (destination / '.INSTALL').is_file()
    else:
        raise ValueError(f'Unknown package format: {package}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path, nargs='?')
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--version')
    args = parser.parse_args()
    if args.version is None:
        args.version = tomllib.loads((ROOT / 'Cargo.toml').read_text())['workspace']['package']['version']
    if args.installed:
        payload(Path('/'), installed=True)
        binaries(Path('/'), args.version)
        image_tools(Path('/'))
        controller_smoke(Path('/'))
        if args.gui:
            gui_smoke(Path('/'))
    else:
        if args.package is None:
            parser.error('Provide a package or --installed')
        with tempfile.TemporaryDirectory(prefix='anvil-package-check-') as directory:
            destination = Path(directory)
            extract(args.package.resolve(), destination, args.version)
            payload(destination)
            binaries(destination, args.version)
            image_tools(destination)
        print(f'{args.package.name}: OK')


if __name__ == '__main__':
    main()
