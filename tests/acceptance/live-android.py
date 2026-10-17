#!/usr/bin/env python3
"""Explicit disposable live Android acceptance; never uses installed controller IPC.

sudo python3 tests/acceptance/live-android.py /tmp/anvil-live-NAME --prepare
sudo python3 tests/acceptance/live-android.py /tmp/anvil-live-NAME --start --x11

Requires an already loaded binder_linux module. Preparation copies images and
extracts the supplied current package. Start creates a transient production
resource scope outside private PID namespaces, then runs the production worker
inside private mount/PID/network namespaces. Only this new store is writable.
Stop through acceptance.json's root-only worker_endpoint. Data is retained.
No fixture or Starting state constitutes Android/input acceptance.
"""
import argparse
import fcntl
import hashlib
import pwd
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import shutil
import subprocess
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()

def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), REPO / 'services' / (name + '.py'))
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value

def require(value, message):
    if not value: raise RuntimeError(message)

def validate_root(root, existing=True):
    require(root.is_absolute() and root.parent == Path('/tmp') and root.name.startswith('anvil-live-') and root.resolve() == root, 'Require /tmp/anvil-live-* disposable root')
    if existing:
        info = root.lstat()
        require(root.is_dir() and info.st_uid == 0 and not info.st_mode & 0o077, 'Unsafe disposable root')

def read_metadata(root):
    validate_root(root)
    info = (root / 'acceptance.json').lstat()
    require(not (root / 'acceptance.json').is_symlink() and info.st_uid == 0 and not info.st_mode & 0o022,
            'Unsafe disposable metadata')
    value = json.loads((root / 'acceptance.json').read_text())
    instance = Path(value['instance'])
    require(instance.parent == root / 'instances' and instance.name == value['id'] and instance.resolve() == instance,
            'Invalid disposable instance metadata')
    return value

def isolate(command):
    # PID1 remains a holder; the inner mount namespace differs from PID1 as
    # required by production provision.build. The outer netns protects host
    # routes/firewall even if a future production setup adds uplink behavior.
    # Production workers are normally PID1 and reap orphaned attach children.
    # Here PID1 is the namespace holder, so it must do that job instead.
    holder = '''import os,pathlib,subprocess,sys,time
child=subprocess.Popen(sys.argv[1:])
while child.poll() is None:
    for entry in pathlib.Path('/proc').iterdir():
        if not entry.name.isdecimal() or int(entry.name)==child.pid: continue
        try:
            status=dict(line.split(':',1) for line in (entry/'status').read_text().splitlines() if ':' in line)
            if status.get('PPid','').strip()=='1' and status.get('State','').strip().startswith('Z'):
                os.waitpid(int(entry.name),os.WNOHANG)
        except (FileNotFoundError,ProcessLookupError,ChildProcessError): pass
    time.sleep(.05)
sys.exit(child.returncode)
'''
    return ['unshare', '--mount', '--net', '--pid', '--fork', '--mount-proc', '--propagation', 'private',
            sys.executable, '-c', holder, 'unshare', '--mount', '--propagation', 'private', *command]

def package_digest(package):
    result = hashlib.sha256()
    with package.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''): result.update(block)
    return result.hexdigest()

def assets(root, package):
    staged = root / 'staged-package'; digest = package_digest(package)
    marker = root / 'staged-package.sha256'
    if not staged.exists() or not marker.exists() or marker.read_text().strip() != digest:
        fresh = root / ('staged-new-' + uuid.uuid4().hex)
        subprocess.run(['dpkg-deb', '-x', str(package), str(fresh)], check=True)
        require(package_digest(package) == digest, 'Package changed while extracting; retry with a stable package')
        if staged.exists(): staged.rename(root / ('staged-old-' + uuid.uuid4().hex))
        fresh.rename(staged); marker.write_text(digest + '\n')
    return staged / 'usr/local/lib/anvildroid-controller'

def close_displays(processes):
    for child in reversed(processes):
        if child.poll() is None: child.terminate()
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)

def nested_wayland(root, owner_uid, generation):
    """Only private Xvfb → UID-owned Weston; never touch host input devices."""
    validate_root(root)
    require(os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'), 'Private inner mount namespace required')
    account = pwd.getpwuid(owner_uid)
    require(owner_uid > 0, 'Non-root Weston owner required')
    authority = root / ('nested-authority-' + generation)
    authority.touch(mode=0o600); os.chown(authority, owner_uid, account.pw_gid)
    subprocess.run(['xauth', '-f', str(authority), 'add', ':77', '.', secrets.token_hex(16)], check=True, timeout=5)
    # Weston cannot traverse the root-only fixture directory. Pin the private
    # authority into the already masked, UID-owned runtime directory instead.
    user_run = Path('/run/user') / str(owner_uid)
    user_authority = user_run / 'nested-authority'
    shutil.copyfile(authority, user_authority); os.chown(user_authority, owner_uid, account.pw_gid); user_authority.chmod(0o600)
    subprocess.run(['mount', '-t', 'tmpfs', '-o', 'mode=1777', 'tmpfs', '/tmp/.X11-unix'], check=True)
    processes = []
    display = 'wayland-anvil-' + generation
    env = {'PATH': os.environ['PATH'], 'XDG_RUNTIME_DIR': str(user_run), 'HOME': str(user_run),
           'LIBGL_ALWAYS_SOFTWARE': '1', 'GALLIUM_DRIVER': 'llvmpipe', 'DISPLAY': ':77', 'XAUTHORITY': str(user_authority)}
    try:
        with (root / 'nested-xvfb.log').open('a') as log:
            processes.append(subprocess.Popen(['Xvfb', ':77', '-nolock', '-screen', '0', '1700x1100x24',
                '-nolisten', 'tcp', '-noreset', '-auth', str(authority)], stdout=log, stderr=log))
        deadline = time.monotonic() + 10
        while not Path('/tmp/.X11-unix/X77').exists():
            require(processes[0].poll() is None and time.monotonic() < deadline, 'Private Xvfb failed')
            time.sleep(.1)
        with (root / 'nested-weston.log').open('a') as log:
            processes.append(subprocess.Popen(['weston', '--backend=x11', '--renderer=gl', '--no-config',
                '--socket=' + display, '--width=1600', '--height=1000', '--idle-time=0', '--debug'],
                env=env, cwd='/', user=owner_uid, group=account.pw_gid, extra_groups=[], stdout=log, stderr=log))
        deadline = time.monotonic() + 15
        while not (user_run / display).exists():
            require(processes[-1].poll() is None and time.monotonic() < deadline, 'Private nested Weston failed')
            time.sleep(.1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(3); probe.connect(str(user_run / display))
            protocols = module('runtime-desktop').registry_protocols(probe)
        require(protocols.get('wl_seat'), 'Nested Weston must expose an input seat')
        extensions = subprocess.run(['xdpyinfo', '-queryExtensions'], env=env, capture_output=True, text=True, check=True, timeout=5).stdout
        require('XTEST' in extensions, 'Private Xvfb must support XTest')
        deadline=time.monotonic()+5
        while True:
            tree=subprocess.run(['xwininfo','-root','-tree'],env=env,capture_output=True,text=True,check=True,timeout=5).stdout
            (root/'nested-x11-tree.txt').write_text(tree)
            windows = subprocess.run(['xdotool', 'search', '--onlyvisible', '--class', 'weston'],
                env=env, capture_output=True, text=True, timeout=5).stdout.splitlines()
            if len(windows)==1: break
            require(time.monotonic()<deadline,'Expected exactly one private Weston X11 output: '+tree)
            time.sleep(.1)
        return processes, dict(kind='nested-weston-x11', x11_display=':77', authority=str(user_authority),
            wayland_display=display, compositor_namespace_pid=processes[-1].pid, x11_window=windows[0],
            compositor_mount_namespace=os.readlink('/proc/self/ns/mnt'),
            compositor_pid_namespace=os.readlink('/proc/self/ns/pid'), protocols=protocols)
    except BaseException:
        close_displays(processes); raise

def preflight_wayland_input(root, owner_uid):
    require(not root.exists(), 'Preflight requires a new disposable root')
    root.mkdir(mode=0o700)
    account = pwd.getpwuid(owner_uid); require(owner_uid > 0, 'Non-root Weston owner required')
    private = root / 'private-user-run'; private.mkdir(mode=0o700); os.chown(private, owner_uid, account.pw_gid)
    subprocess.run(['mount', '--bind', str(private), '/run/user/' + str(owner_uid)], check=True)
    processes, result = nested_wayland(root, owner_uid, uuid.uuid4().hex)
    try:
        result.update(android_started=False, host_input_devices_used=False)
    finally:
        close_displays(processes)
    require(all(child.poll() is not None for child in processes), 'Private displays did not exit')
    result['cleanup_verified'] = True
    (root / 'input-preflight.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)

def prepare(root, package, owner_uid, image_source, flavor):
    require(owner_uid > 0, 'A non-root registered desktop owner is required')
    pwd.getpwuid(owner_uid)
    validate_root(root, existing=False)
    require(not root.exists(), 'Preparation needs a new disposable root')
    root.mkdir(mode=0o700)
    source = root / 'source'; source.mkdir(mode=0o700)
    for name in ('system.img', 'vendor.img'):
        shutil.copyfile(image_source / name, source / name)
        (source / name).chmod(0o600)
    backend = module('runtime-images').ImageBackend(root, source)
    identifier, generation = 'r-' + uuid.uuid4().hex, uuid.uuid4().hex
    backend.prepare(identifier); instance = backend.location(identifier)
    payload = assets(root, package)
    provision = module('runtime-provision'); provision.ASSETS = payload
    provision.build(instance, flavor)
    (instance / 'gpu.json').write_text(json.dumps({'node': 'software'}))
    (instance / 'resources.json').write_text(json.dumps({'memory_mib': 4096, 'cpu_count': 4}))
    value = {'root': str(root), 'instance': str(instance), 'id': identifier, 'generation': generation,
             'worker_endpoint': str(instance / 'worker.sock'), 'package': str(package), 'package_sha256': (root / 'staged-package.sha256').read_text().strip(), 'owner_uid': owner_uid, 'image_source': str(image_source), 'flavor': flavor}
    (root / 'acceptance.json').write_text(json.dumps(value, indent=2))
    print(json.dumps(value), flush=True)

def start(root, package, x11, wayland=False, wayland_x11=False):
    wayland = wayland or wayland_x11
    metadata = read_metadata(root); instance = Path(metadata['instance'])
    owner_uid = metadata.get('owner_uid', metadata.get('desktop_uid'))
    require(type(owner_uid) is int and owner_uid > 0, 'Missing desktop owner')
    account = pwd.getpwuid(owner_uid)
    lock = os.open(instance / 'worker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    payload = assets(root, package)
    metadata['package_sha256'] = (root / 'staged-package.sha256').read_text().strip()
    (root / 'acceptance.json').write_text(json.dumps(metadata, indent=2))
    subprocess.run(['mount', '--bind', str(payload), '/usr/local/lib/anvildroid-controller'], check=True)
    backend = module('runtime-android').AndroidBackend(module('runtime-images').ImageBackend(root, root / 'source'))
    # Pin the exact current package helpers even for headless operation.
    shutil.copyfile(payload / 'anvildroid-shutdown.jar', instance / 'shutdown.jar')
    # Hide all user session sockets in this namespace. This prevents production
    # optional audio discovery from attaching to the user's PulseAudio server.
    user_run = root / 'private-user-run'; user_run.mkdir(mode=0o700, exist_ok=True)
    os.chown(user_run, owner_uid, account.pw_gid)
    subprocess.run(['mount', '--bind', str(user_run), str(Path('/run/user') / str(owner_uid))], check=True)
    command = ['unshare', '--mount', '--net', '--propagation', 'private', sys.executable,
               '/usr/local/lib/anvildroid-controller/runtime-worker.py', '--instance', str(instance), '--id', metadata['id'],
               '--generation', metadata['generation'], '--namespaces', os.readlink('/proc/self/ns/mnt'), os.readlink('/proc/self/ns/net')]
    xserver = None
    nested_processes = []
    if x11 or wayland:
        provision = module('runtime-provision'); provision.ASSETS = payload
        if wayland:
            # Keep prior evidence, but rebuild every support overlay asset
            # from this package when reusing the dedicated Wayland fixture.
            (instance / 'support').rename(instance / ('support-before-' + metadata['generation']))
            provision.build(instance, metadata['flavor'])
        provision.refresh_window_contract(instance)
        backend.pin_desktop_bridge(instance)
    if x11:
        (instance / 'display-backend.json').write_text(json.dumps({'backend': 'native-x11', 'presenter': 'amd'}))
        authority = instance / 'x11-authority'; authority.touch(mode=0o600)
        subprocess.run(['xauth', '-f', str(authority), 'add', ':77', '.', secrets.token_hex(16)], check=True)
        subprocess.run(['mount', '-t', 'tmpfs', '-o', 'mode=1777', 'tmpfs', '/tmp/.X11-unix'], check=True)
        with (root / 'xvfb.log').open('a') as log:
            xserver = subprocess.Popen(['Xvfb', ':77', '-nolock', '-screen', '0', '1600x1000x24', '-nolisten',
                                        'tcp', '-noreset', '-auth', str(authority)], stdout=log, stderr=log)
        time.sleep(1); require(xserver.poll() is None, 'Private Xvfb exited')
        metadata.update(x11_display=':77', desktop_uid=owner_uid)
        (root / 'acceptance.json').write_text(json.dumps(metadata, indent=2))
        command += ['--desktop-display', 'x11::77', '--desktop-uid', str(owner_uid)]
    elif wayland:
        (instance / 'display-backend.json').write_text(json.dumps({'backend': 'legacy', 'presenter': 'amd'}))
        if wayland_x11:
            nested_processes, transport = nested_wayland(root, owner_uid, metadata['generation'])
            metadata.update(wayland_display=transport['wayland_display'], desktop_uid=owner_uid, input_transport=transport)
            (root / 'acceptance.json').write_text(json.dumps(metadata, indent=2))
            command += ['--desktop-display', transport['wayland_display'], '--desktop-uid', str(owner_uid)]
    if wayland and not wayland_x11:
        env = {'PATH': os.environ['PATH'], 'XDG_RUNTIME_DIR': '/run/user/' + str(owner_uid),
               'HOME': '/run/user/' + str(owner_uid), 'LIBGL_ALWAYS_SOFTWARE': '1', 'GALLIUM_DRIVER': 'llvmpipe'}
        with (root / 'wayland-weston.log').open('a') as log:
            xserver = subprocess.Popen(['weston', '--backend=headless', '--renderer=gl', '--no-config',
                '--socket=wayland-anvil-acceptance', '--width=1600', '--height=1000', '--idle-time=0', '--debug'],
                env=env, cwd='/', user=owner_uid, group=account.pw_gid, extra_groups=[], stdout=log, stderr=log)
        deadline = time.monotonic() + 15
        while not (user_run / 'wayland-anvil-acceptance').exists():
            require(xserver.poll() is None, 'Private Weston exited')
            require(time.monotonic() < deadline, 'Private Weston socket timed out')
            time.sleep(.1)
        metadata.update(wayland_display='wayland-anvil-acceptance', desktop_uid=owner_uid)
        (root / 'acceptance.json').write_text(json.dumps(metadata, indent=2))
        command += ['--desktop-display', 'wayland-anvil-acceptance', '--desktop-uid', str(owner_uid)]
    elif not x11 and not wayland:
        (instance / 'display-backend.json').write_text(json.dumps({'backend': 'legacy', 'presenter': 'amd'}))
    os.close(lock)  # Production worker takes this same lock; no other launcher is authorized.
    try:
        with (instance / 'worker.log').open('a') as log:
            return subprocess.call(command, stdout=log, stderr=subprocess.STDOUT)
    finally:
        close_displays(nested_processes)
        if xserver is not None: xserver.terminate(); xserver.wait(timeout=5)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true'); mode.add_argument('--start', action='store_true')
    mode.add_argument('--preflight-wayland-input', action='store_true', help='Isolated display/input fixture only; never starts Android')
    display = parser.add_mutually_exclusive_group()
    display.add_argument('--x11', action='store_true')
    display.add_argument('--wayland', action='store_true', help='Private software Weston desktop acceptance')
    display.add_argument('--wayland-x11', action='store_true', help='Guest Wayland with private Xvfb/Weston X11 input transport')
    parser.add_argument('--image-source', type=Path, default=Path('/var/lib/waydroid/images'))
    parser.add_argument('--flavor', choices=('VANILLA','GAPPS'), default='VANILLA')
    parser.add_argument('--owner-uid', type=int, default=int(os.environ.get('SUDO_UID', '0')))
    mode.add_argument('--stop', action='store_true', help='Request production worker stop for this disposable store')
    parser.add_argument('--package', type=Path, default=REPO / 'target/releases/anvildroid-controller_0.1.3_amd64.deb')
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(); require(os.geteuid() == 0, 'Root required')
    validate_root(args.root, existing=args.start or args.stop)
    if args.stop:
        metadata = read_metadata(args.root)
        backend = module('runtime-android').AndroidBackend(module('runtime-images').ImageBackend(args.root, args.root / 'source'))
        observed = backend.status(metadata['id'])
        require(observed.get('generation') == metadata['generation'], 'Disposable generation changed; refresh metadata')
        print(json.dumps(backend.status(metadata['id'], 'stop')))
        return 0
    if args.inside:
        require(os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'), 'Private inner mount namespace required')
        require(os.readlink('/proc/self/ns/net') != os.readlink('/proc/1/ns/net'), 'Private inner network namespace required')
        subprocess.run(['mount', '--make-rprivate', '/'], check=True)
        if args.preflight_wayland_input: return preflight_wayland_input(args.root, args.owner_uid)
        return prepare(args.root, args.package, args.owner_uid, args.image_source, args.flavor) if args.prepare else start(args.root, args.package, args.x11, args.wayland, args.wayland_x11)
    command = [sys.executable, str(SCRIPT), str(args.root), '--inside', '--package', str(args.package),
               '--preflight-wayland-input' if args.preflight_wayland_input else '--prepare' if args.prepare else '--start', '--owner-uid', str(args.owner_uid), '--image-source', str(args.image_source), '--flavor', args.flavor] + (['--x11'] if args.x11 else []) + (['--wayland'] if args.wayland else []) + (['--wayland-x11'] if args.wayland_x11 else [])
    # Give the inner setup a second network namespace as well as mountns.
    command = isolate(['unshare', '--net', *command])
    if args.start:
        launch_lock = os.open(args.root / 'launch.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(launch_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = read_metadata(args.root)
        worker_lock = os.open(Path(metadata['instance']) / 'worker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata['package'] = str(args.package); metadata['package_sha256'] = package_digest(args.package)
        metadata['owner_uid'] = metadata.get('owner_uid', metadata.get('desktop_uid', args.owner_uid))
        resources = module('runtime-resources')
        metadata['generation'] = uuid.uuid4().hex; metadata['scope'] = resources.unit(metadata['id'], metadata['generation'])
        (args.root / 'acceptance.json').write_text(json.dumps(metadata, indent=2))
        os.close(worker_lock)
        command = resources.prefix(metadata['id'], metadata['generation'], resources.load(Path(metadata['instance']))) + command
    return subprocess.call(command)

if __name__ == '__main__': sys.exit(main())
