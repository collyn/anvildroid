#!/usr/bin/env python3
"""Disposable stock Default acceptance. --preflight never sends Start.

Preparation copies only image bytes and a digest-pinned package. Preflight runs
under a bounded delegated scope, private namespaces/devices/bus and read-only
host filesystems. Review preflight.json before any separately authorized --run.
No installed service, personal runtime or host kernel setting is modified.
"""
import argparse
import ctypes
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import time
import uuid

SCRIPT = Path(__file__).resolve()
REPO = SCRIPT.parents[2]
ASSETS = Path('/usr/local/lib/anvildroid-controller')
WORK = Path('/var/lib/waydroid')
STORE = Path('/var/lib/anvildroid-controller')
NAMESPACES = ('mnt', 'net', 'pid', 'ipc', 'uts', 'cgroup')
DROPPED_CAPS = {'sys_module': 16, 'sys_rawio': 17, 'sys_boot': 22, 'sys_time': 25}
BINDER_NAMES = ('anvil-fixture-binder', 'anvil-fixture-vndbinder', 'anvil-fixture-hwbinder')
NET_SYSCTLS = ('/proc/sys/net/ipv4/ip_forward',)
NET_SYSCTL_TREES = ('/proc/sys/net/ipv6/conf',)


def require(value, message):
    if not value: raise RuntimeError(message)


def run(*args, timeout=20, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, timeout=timeout, **kwargs)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): value.update(block)
    return value.hexdigest()


def identity(path):
    value = Path(path).stat()
    return {'device': value.st_dev, 'inode': value.st_ino, 'rdev': value.st_rdev,
            'mode': value.st_mode, 'uid': value.st_uid, 'gid': value.st_gid}


def namespaces(): return {name: os.readlink('/proc/self/ns/' + name) for name in NAMESPACES}


def validate_root(root, exists=True):
    require(root.is_absolute() and root.parent == Path('/tmp') and root.name.startswith('anvil-default-')
            and len(root.name) > len('anvil-default-') and re.fullmatch(r'[A-Za-z0-9_-]+', root.name)
            and root.resolve() == root, 'Require an unsymlinked /tmp/anvil-default-* root')
    if exists:
        value = root.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o077, 'Unsafe fixture root')


def metadata(root):
    validate_root(root)
    path = root / 'acceptance.json'; value = path.lstat()
    require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o077 and value.st_nlink == 1, 'Unsafe fixture metadata')
    result = json.loads(path.read_text())
    require(result['root'] == str(root) and result['scope'].startswith('anvil-default-') and result['scope'].endswith('.scope'), 'Unexpected fixture identity')
    require(result['owner_uid'] > 0, 'Invalid fixture owner')
    return result


def write_json(path, value):
    temporary = path.with_name(path.name + '.new')
    with temporary.open('w') as stream: json.dump(value, stream, indent=2); stream.write('\n')
    temporary.chmod(0o600); temporary.replace(path)


def load_module(name, base=ASSETS):
    spec = importlib.util.spec_from_file_location('acceptance_' + name.replace('-', '_'), base / (name + '.py'))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def verify_copies(root, value):
    require(digest(root / 'controller.deb') == value['package_sha256'], 'Pinned package changed')
    prefix = './usr/local/lib/anvildroid-controller/'
    payload = root / 'package/usr/local/lib/anvildroid-controller'
    expected = {}
    process = subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(root / 'controller.deb')], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            for member in archive:
                if not member.name.startswith(prefix) or member.isdir(): continue
                name = member.name[len(prefix):]; target = payload / name
                require('..' not in Path(name).parts and not Path(name).is_absolute(), 'Unsafe package payload path')
                info = target.lstat()
                require(info.st_uid == 0 and (stat.S_ISLNK(info.st_mode) or not info.st_mode & 0o022), 'Unsafe extracted payload permissions')
                if member.isfile():
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Extracted package file is not private regular data')
                    stream = archive.extractfile(member); value_hash = hashlib.sha256()
                    for block in iter(lambda: stream.read(1024 * 1024), b''): value_hash.update(block)
                    expected[name] = value_hash.hexdigest()
                    require(digest(target) == expected[name], 'Extracted package bytes differ from pinned archive: ' + name)
                elif member.issym():
                    require(target.is_symlink() and os.readlink(target) == member.linkname and target.resolve().is_relative_to(payload), 'Unsafe extracted package symlink')
                    expected[name] = {'symlink': member.linkname}
                else: raise RuntimeError('Unsupported package payload entry')
        require(process.wait(timeout=10) == 0, 'Cannot read pinned package archive')
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=3)
    observed = {str(p.relative_to(payload)) for p in payload.rglob('*') if p.is_symlink() or not p.is_dir()}
    require(observed == set(expected), 'Unexpected extracted package files')
    images = {}
    for name in ('system.img', 'vendor.img'):
        path = root / 'work/images' / name; info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o022, 'Unsafe copied image')
        require(path.resolve() == path, 'Copied image has a symlink ancestor')
        image_hash = digest(path); require(image_hash == value['images'][name]['copy_sha256'], 'Copied image bytes changed')
        images[name] = {'identity': identity(path), 'sha256': image_hash}
    return {'payload_manifest_sha256': hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest(), 'copied_images': images}


def prepare(args):
    root = args.root; validate_root(root, False)
    require(not root.exists(), 'Require a new disposable fixture root')
    require(args.expected_package_sha256 and digest(args.package) == args.expected_package_sha256, 'Pinned package digest mismatch')
    account = pwd.getpwuid(args.owner_uid); require(account.pw_uid > 0, 'A registered non-root fixture identity is required')
    require(Path(account.pw_dir).parent == Path('/home'), 'Fixture supports a conventional /home account only')
    root.mkdir(mode=0o700)
    package = root / 'controller.deb'; shutil.copyfile(args.package, package)
    require(digest(package) == args.expected_package_sha256, 'Package changed while copied')
    run('dpkg-deb', '-x', package, root / 'package')
    images = root / 'work/images'; images.mkdir(parents=True)
    provenance = {}
    for name in ('system.img', 'vendor.img'):
        source = args.image_source / name
        value = source.stat(); require(stat.S_ISREG(value.st_mode), 'Image source must be a regular file')
        before = (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        shutil.copyfile(source, images / name)
        value = source.stat(); require(before == (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns), 'Source image changed while copying')
        provenance[name] = {'source': str(source), 'source_identity': before, 'copy_sha256': digest(images / name)}
        require(identity(source)['inode'] != identity(images / name)['inode'] or identity(source)['device'] != identity(images / name)['device'], 'Image copy aliases original')
    for name in ('store', 'state', 'home', 'work/rootfs', 'work/overlay', 'work/overlay/vendor', 'work/overlay_rw/system',
                 'work/overlay_rw/vendor', 'work/overlay_work/system', 'work/overlay_work/vendor', 'work/lxc/waydroid'):
        (root / name).mkdir(mode=0o755, parents=True, exist_ok=True)
    (root / 'store').chmod(0o700); (root / 'state').chmod(0o700)
    home = root / 'home'; home.chmod(0o755); os.chown(home, account.pw_uid, account.pw_gid)
    for name in ('data', 'share', 'runtime'):
        p = home / name; p.mkdir(mode=0o755); os.chown(p, account.pw_uid, account.pw_gid)
    (home / 'runtime').chmod(0o700)
    token = secrets.token_hex(12); (home / 'share/acceptance-read').write_text(token)
    # Explicit fixture permission permits Android storage service guest-to-host
    # writes. Product code must never chmod the chosen source.
    (home / 'share').chmod(0o777)
    value = {'root': str(root), 'id': uuid.uuid4().hex, 'scope': 'anvil-default-' + uuid.uuid4().hex + '.scope',
             'owner_uid': account.pw_uid, 'owner_gid': account.pw_gid, 'owner_name': account.pw_name,
             'home': account.pw_dir, 'package_sha256': args.expected_package_sha256, 'images': provenance,
             'share_token': token, 'prepared_at': time.time()}
    write_json(root / 'acceptance.json', value)
    stage_probe(root, value, args.probe_apk)
    print(json.dumps(value), flush=True)


def stage_probe(root, value, source):
    require(source.is_file() and not source.is_symlink(), 'Build the maintained touch probe APK first')
    shutil.copyfile(source, root / 'probe.apk')
    value['probe_apk_sha256'] = digest(root / 'probe.apk'); write_json(root / 'acceptance.json', value)


def restage(args):
    value = metadata(args.root)
    require(not value.get('loops') and not Path(value.get('host_cgroup', '/nonexistent')).exists(), 'Fixture still active; refusing restage')
    require(args.expected_package_sha256 and digest(args.package) == args.expected_package_sha256, 'Pinned package digest mismatch')
    (args.root / 'package').rename(args.root / ('package-before-' + uuid.uuid4().hex))
    shutil.copyfile(args.package, args.root / 'controller.deb')
    require(digest(args.root / 'controller.deb') == args.expected_package_sha256, 'Package changed while copied')
    run('dpkg-deb', '-x', args.root / 'controller.deb', args.root / 'package')
    value['package_sha256'] = args.expected_package_sha256
    stage_probe(args.root, value, args.probe_apk)


def scope_command(root, value, mode, reviewed=None, checkpoint_seconds=0):
    args = ['systemd-run', '--scope', '--collect', '--quiet', '--unit=' + value['scope'],
            '-p', 'Delegate=yes', '-p', 'MemoryMax=6G', '-p', 'MemorySwapMax=0', '-p', 'CPUQuota=400%',
            '-p', 'TasksMax=2048', '-p', 'OOMPolicy=kill', '--', sys.executable, str(SCRIPT), str(root), '--scoped', '--' + mode]
    if checkpoint_seconds: args += ['--checkpoint-seconds', str(checkpoint_seconds)]
    if reviewed: args += ['--reviewed-preflight', reviewed]
    return args


def scoped(args):
    value = metadata(args.root)
    require(value['host_namespaces'] == namespaces(), 'Unexpected namespaces before delegation')
    cgroup = Path('/sys/fs/cgroup') / Path(Path('/proc/self/cgroup').read_text().strip().split('::', 1)[1]).relative_to('/')
    require(cgroup.name == value['scope'] and cgroup.is_dir(), 'Launcher is outside its task-owned scope')
    require((cgroup / 'memory.max').read_text().strip() == str(6 * 1024**3), 'Memory bound missing')
    require((cgroup / 'memory.swap.max').read_text().strip() == '0', 'Swap bound missing')
    require((cgroup / 'pids.max').read_text().strip() == '2048', 'Task bound missing')
    quota, period = (cgroup / 'cpu.max').read_text().split(); require(quota != 'max' and int(quota) <= 4 * int(period), 'CPU bound missing')
    # Root this cgroup namespace at the delegated scope, then move every
    # task-owned scope process to a leaf so its root can delegate controllers.
    os.unshare(0x02000000)  # CLONE_NEWCGROUP
    leaf = cgroup / 'supervisor'; leaf.mkdir()
    for pid in (cgroup / 'cgroup.procs').read_text().split(): (leaf / 'cgroup.procs').write_text(pid)
    require(not (cgroup / 'cgroup.procs').read_text().strip(), 'Scope root is populated')
    (cgroup / 'cgroup.subtree_control').write_text('+cpu +memory +pids')
    value['host_cgroup'] = str(cgroup); write_json(args.root / 'acceptance.json', value)
    command = ['setpriv', '--bounding-set=-sys_module,-sys_time,-sys_boot,-sys_rawio',
               'unshare', '--mount', '--net', '--pid', '--ipc', '--uts', '--fork', '--kill-child=SIGKILL', '--mount-proc',
               sys.executable, str(SCRIPT), str(args.root), '--inside', '--' + ('run' if args.run else 'preflight')]
    if args.reviewed_preflight: command += ['--reviewed-preflight', args.reviewed_preflight]
    if args.checkpoint_seconds: command += ['--checkpoint-seconds', str(args.checkpoint_seconds)]
    host_proc = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY)
    command += ['--host-proc-fd', str(host_proc)]
    try: return subprocess.call(command, pass_fds=(host_proc,))
    finally: os.close(host_proc)


def readonly_tree(path):
    class Attr(ctypes.Structure):
        _fields_ = [('attr_set', ctypes.c_ulonglong), ('attr_clr', ctypes.c_ulonglong),
                    ('propagation', ctypes.c_ulonglong), ('userns_fd', ctypes.c_ulonglong)]
    libc = ctypes.CDLL(None, use_errno=True)
    attr = Attr(1, 0, 0, 0)
    result = libc.syscall(442, -100, ctypes.c_char_p(os.fsencode(path)), 0x8000, ctypes.byref(attr), ctypes.sizeof(attr))
    if result < 0: raise OSError(ctypes.get_errno(), 'Recursive read-only mount policy failed')


def bind(source, target, writable=False):
    # util-linux canonicalizes proc-FD paths and can resolve a covered /tmp
    # source to the new empty target. Call mount directly while this process
    # still owns the descriptor; subprocesses deliberately do not inherit it.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]
    if libc.mount(os.fsencode(source), os.fsencode(target), None, 4096, None) < 0:
        raise OSError(ctypes.get_errno(), 'Private fixture bind failed: ' + str(target))
    run('mount', '-o', 'remount,bind,' + ('rw' if writable else 'ro'), target)


def device_program(entries):
    """Small fail-closed cgroup-device BPF: exact type/major/minor allowlist."""
    instructions = [(0x61, 0x12, 0, 0), (0x54, 2, 0, 0xffff),
                    (0x61, 0x13, 4, 0), (0x61, 0x14, 8, 0),
                    (0x61, 0x15, 0, 0), (0x74, 5, 0, 16)]
    for entry in entries:
        kind, major, minor = entry[:3]
        checks = [(2, kind), (3, major)]
        if minor is not None: checks.append((4, minor))
        if len(entry) == 4: checks.append((5, entry[3]))
        instructions += [(0x55, register, len(checks) - index + 1, expected)
                         for index, (register, expected) in enumerate(checks)]
        instructions += [(0xb7, 0, 0, 1), (0x95, 0, 0, 0)]
    instructions += [(0xb7, 0, 0, 0), (0x95, 0, 0, 0)]
    return b''.join(struct.pack('<BBhi', *item) for item in instructions)


def attach_device_policy(cgroup_fd, entries):
    # Linux UAPI only; no stock driver implementation is copied. This harness
    # is amd64-only, matching the pinned Debian artifact under acceptance.
    require(os.uname().machine == 'x86_64', 'Device-policy fixture currently requires amd64')
    program = device_program(entries); code = ctypes.create_string_buffer(program)
    license = ctypes.create_string_buffer(b'MIT'); log = ctypes.create_string_buffer(65536)
    attr = ctypes.create_string_buffer(struct.pack('<IIQQIIQII16sII', 15, len(program) // 8,
        ctypes.addressof(code), ctypes.addressof(license), 1, len(log), ctypes.addressof(log), 0, 0, b'anvil_devices', 0, 6))
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.syscall(321, 5, ctypes.byref(attr), len(attr) - 1)
    if fd < 0: raise OSError(ctypes.get_errno(), 'Fail-closed device policy load failed: ' + log.value.decode(errors='replace')[:1000])
    try:
        attach = ctypes.create_string_buffer(struct.pack('<IIII', cgroup_fd, fd, 6, 2))
        if libc.syscall(321, 8, ctypes.byref(attach), 16) < 0:
            raise OSError(ctypes.get_errno(), 'Device policy attachment failed')
        ids = (ctypes.c_uint32 * 16)()
        query = ctypes.create_string_buffer(struct.pack('<IIIIQI', cgroup_fd, 6, 0, 0, ctypes.addressof(ids), 16) + bytes(4))
        if libc.syscall(321, 16, ctypes.byref(query), 32) < 0:
            raise OSError(ctypes.get_errno(), 'Device policy readback failed')
        count = struct.unpack_from('<I', query.raw, 24)[0]
        require(count > 0, 'Device policy was not attached')
        return {'program_sha256': hashlib.sha256(program).hexdigest(), 'program_ids': list(ids)[:count], 'allow': entries}
    finally: os.close(fd)


def prepare_devices(root, value, cgroup_fd):
    devices = {name: identity('/dev/' + name) for name in ('null', 'zero', 'full', 'random', 'urandom', 'tty', 'fuse')}
    loops = []
    # Persist each allocation immediately so outer teardown can authenticate
    # and detach only these exact copied-image backing files after a failure.
    value['loops'] = []; write_json(root / 'acceptance.json', value)
    for name in ('system.img', 'vendor.img'):
        image = root / 'work/images' / name
        require(len(os.fsencode(image)) < 64, 'Task loop path exceeds authenticated kernel filename length')
        result = run('losetup', '--find', '--show', '--read-only', image, capture_output=True, text=True).stdout.strip()
        require(result.startswith('/dev/loop') and result[9:].isdigit(), 'Unexpected task loop identity')
        backing = Path('/sys/class/block', Path(result).name, 'loop/backing_file').read_text().strip()
        require(backing == str(image), 'Allocated loop backs another file')
        record = {'path': result, 'backing_file': backing, 'image_identity': identity(image), 'device_identity': identity(result), 'loop_identity': loop_identity(result)}
        require(record['loop_identity']['flags'] & 1 and record['loop_identity']['backing_inode'] == image.stat().st_ino and
                record['loop_identity']['backing_device'] == image.stat().st_dev, 'Loop is not a read-only copied-image attachment')
        value['loops'].append(record); write_json(root / 'acceptance.json', value); loops.append(record)
        devices[Path(result).name] = record['device_identity']
    binder = root / 'binder-source'; binder.mkdir(exist_ok=True)
    run('mount', '-t', 'binder', 'binder', binder)
    with (binder / 'binder-control').open('rb') as control:
        for name in BINDER_NAMES: fcntl.ioctl(control, 0xC1086201, struct.pack('256sII', name.encode(), 0, 0))
    binder_fd = os.open(binder, os.O_RDONLY | os.O_DIRECTORY)
    entries = [(2 if stat.S_ISCHR(item['mode']) else 1, os.major(item['rdev']), os.minor(item['rdev'])) for item in devices.values()]
    for name in (*BINDER_NAMES, 'binder-control'):
        item = identity(binder / name); entries.append((2, os.major(item['rdev']), os.minor(item['rdev'])))
    # Private devpts needs its dynamic slave minors; no host terminal/socket
    # paths are retained, and a fresh newinstance devpts is used below.
    entries += [(2, 5, 2), (2, 136, None), (2, 1, 11, 1)]
    forbidden = root / 'forbidden-loop-control'
    forbidden.unlink(missing_ok=True)
    node = identity('/dev/loop-control'); os.mknod(forbidden, stat.S_IFCHR | 0o600, node['rdev'])
    forbidden_block = root / 'forbidden-block'; forbidden_block.unlink(missing_ok=True)
    os.mknod(forbidden_block, stat.S_IFBLK | 0o600, os.makedev(8, 0))
    policy = attach_device_policy(cgroup_fd, entries)
    kmsg_probe = root / 'kmsg-mknod-only-probe'; kmsg_probe.unlink(missing_ok=True)
    os.mknod(kmsg_probe, stat.S_IFCHR | 0o600, os.makedev(1, 11))
    try:
        for flags in (os.O_RDONLY, os.O_WRONLY, os.O_RDWR):
            try: fd = os.open(kmsg_probe, flags)
            except PermissionError: pass
            else:
                os.close(fd); raise RuntimeError('Device policy allowed kernel log data access')
    finally: kmsg_probe.unlink()
    policy['kmsg_mknod_only_all_data_access_denied'] = True
    try:
        fd = os.open(forbidden, os.O_RDONLY)
    except PermissionError: policy['loop_control_denied'] = True
    else:
        os.close(fd); raise RuntimeError('Device policy did not deny global loop-control')
    try: fd = os.open(forbidden_block, os.O_RDONLY)
    except PermissionError: policy['unrelated_block_denied'] = True
    else:
        os.close(fd); raise RuntimeError('Device policy did not deny unrelated block device')
    return devices, binder_fd, policy


def loop_identity(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd); require(stat.S_ISBLK(info.st_mode) and os.major(info.st_rdev) == 7, 'Not a loop block node')
        status = bytearray(232); fcntl.ioctl(fd, 0x4C05, status, True)  # LOOP_GET_STATUS64
        device, inode, rdevice, offset, limit, number, encryption, keysize, flags = struct.unpack_from('<QQQQQIIII', status)
        return {'backing_device': device, 'backing_inode': inode, 'offset': offset, 'limit': limit, 'number': number,
                'flags': flags, 'filename': bytes(status[56:120]).split(b'\0', 1)[0].decode()}
    finally: os.close(fd)


def clean_task_loops(root):
    value = metadata(root)
    for item in value.get('loops', []):
        name = Path(item['path']).name
        require(name.startswith('loop') and name[4:].isdigit(), 'Invalid cleanup loop identity')
        backing = Path('/sys/class/block', name, 'loop/backing_file')
        if not backing.exists(): continue
        require(item['backing_file'] in (str(root / 'work/images/system.img'), str(root / 'work/images/vendor.img')), 'Unexpected cleanup backing path')
        require(identity(Path(item['backing_file'])) == item['image_identity'], 'Copied loop image identity changed')
        observed = loop_identity(item['path'])
        require(observed['backing_device'] == item['image_identity']['device'] and observed['backing_inode'] == item['image_identity']['inode']
                and observed['offset'] == 0 and observed['limit'] == 0 and observed['flags'] & 1
                and observed['filename'] == item['backing_file'], 'Loop identity changed; refusing detach')
        if 'loop_identity' in item: require(observed == item['loop_identity'], 'Loop attachment changed; refusing detach')
        run('losetup', '--detach', item['path'])
    value['loops'] = []; write_json(root / 'acceptance.json', value)


def mount_fixture(root, value):
    require(os.getpid() == 1, 'Fixture requires a PID1 supervisor')
    current = namespaces()
    require(all(current[name] != value['host_namespaces'][name] for name in NAMESPACES), 'Host namespace reused')
    run('mount', '--make-rprivate', '/')
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    cgroup_fd = os.open(value['host_cgroup'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    cgroup_identity = os.fstat(cgroup_fd); root_identity = os.fstat(root_fd)
    devices, binder_fd, policy = prepare_devices(root, value, cgroup_fd)
    binder_identity = os.fstat(binder_fd)
    require(Path('/sys/module/bridge').is_dir(), 'Bridge support must already be loaded')
    run('ip', 'link', 'add', 'waydroid0', 'type', 'bridge')
    # sysfs carries the network namespace of its mount, so an inherited host
    # mount is unsuitable for stock network discovery in this private netns.
    run('mount', '-t', 'sysfs', '-o', 'ro,nosuid,nodev,noexec', 'sysfs', '/sys')
    netlinks = json.loads(run('ip', '-j', 'link', 'show', 'waydroid0', capture_output=True, text=True).stdout)
    require(len(netlinks) == 1 and int(Path('/sys/class/net/waydroid0/ifindex').read_text()) == netlinks[0]['ifindex'], 'Private sysfs network identity mismatch')
    try:
        # Host filesystems are recursively read-only. Only explicit private
        # tmpfs roots and this new task directory regain writable mounts.
        readonly_tree('/')
        for path in ('/run', '/dev', '/tmp', '/home', '/var/lib', '/var/log', '/root'):
            run('mount', '-t', 'tmpfs', '-o', 'mode=' + ('1777' if path == '/tmp' else '755'), 'tmpfs', path)
        root.mkdir(mode=0o700)
        bind('/proc/self/fd/' + str(root_fd), root, writable=True)
        observed = root.stat()
        require((observed.st_dev, observed.st_ino) == (root_identity.st_dev, root_identity.st_ino), 'Fixture FD bind identity changed')
    finally: os.close(root_fd)
    # A conventional host resolver symlink may point into the now-private run.
    # Supply only a synthetic fixture resolver; no host resolver socket exists.
    resolver = root / 'resolv.conf'; resolver.write_text('nameserver 192.0.2.1\n')
    resolver_target = Path('/etc/resolv.conf').resolve()
    if Path('/etc/resolv.conf').is_symlink():
        require(resolver_target.is_relative_to('/run'), 'Unsupported external resolver symlink')
        resolver_target.parent.mkdir(parents=True, exist_ok=True); resolver_target.touch()
    bind(resolver, resolver_target)
    # Process-local proc files must remain writable for ordinary LSM attach.
    # A new proc mount is rooted at this verified PID namespace, then every
    # global/nonprocess entry is a separate recursively read-only submount.
    run('mount', '-t', 'proc', '-o', 'rw,nosuid,nodev,noexec', 'proc', '/proc')
    require(os.readlink('/proc/self') == '1' and os.readlink('/proc/1/ns/pid') == current['pid'], 'Fresh procfs exposes another PID namespace')
    proc_protected = []
    for entry in Path('/proc').iterdir():
        if entry.name.isdecimal() or entry.name in ('self', 'thread-self'): continue
        if entry.name in ('net', 'mounts'):
            require(entry.is_symlink() and os.readlink(entry) == 'self/' + entry.name, 'Unexpected process-local proc alias')
            continue
        require(not entry.is_symlink(), 'Unexpected global proc alias')
        bind(entry, entry); readonly_tree(entry); proc_protected.append(entry.name)
    require(not os.statvfs('/proc/self/attr').f_flag & os.ST_RDONLY, 'Process-local LSM attributes are not writable')
    for path in ('/proc/sys', '/proc/sys/vm', '/proc/sys/kernel', '/proc/irq', '/proc/bus'):
        require(os.statvfs(path).f_flag & os.ST_RDONLY, 'Global proc controls are writable: ' + path)
    Path('/dev/pts').mkdir(); run('mount', '-t', 'devpts', '-o', 'newinstance,ptmxmode=0666,mode=0620', 'devpts', '/dev/pts')
    Path('/dev/shm').mkdir(mode=0o1777)
    for name, node in devices.items():
        os.mknod('/dev/' + name, stat.S_IFMT(node['mode']) | 0o600, node['rdev'])
        Path('/dev/' + name).chmod(0o666)
    Path('/dev/ptmx').symlink_to('/dev/pts/ptmx')
    Path('/dev/fd').symlink_to('/proc/self/fd')
    Path('/dev/binderfs').mkdir()
    try:
        bind('/proc/self/fd/' + str(binder_fd), '/dev/binderfs', writable=True)
        observed = Path('/dev/binderfs').stat()
        require((observed.st_dev, observed.st_ino) == (binder_identity.st_dev, binder_identity.st_ino), 'Binder FD bind identity changed')
    finally: os.close(binder_fd)
    # Stock IPv4 forwarding uses one exact file. LXC dynamically creates veth
    # interfaces and changes their per-interface IPv6 settings; only that
    # private-netns subtree is writable. Global sysctls remain read-only.
    require(current['net'] != value['host_namespaces']['net'], 'IPv6 settings require a private network namespace')
    for path in (*NET_SYSCTLS, *NET_SYSCTL_TREES):
        if Path(path).exists(): bind(path, path, writable=True)
    run('ip', 'link', 'add', 'fixture-veth-a', 'type', 'veth', 'peer', 'name', 'fixture-veth-b')
    try:
        dynamic = Path('/proc/sys/net/ipv6/conf/fixture-veth-a/disable_ipv6')
        dynamic.write_text('1'); require(dynamic.read_text().strip() == '1', 'Dynamic private veth IPv6 sysctl failed')
    finally: run('ip', 'link', 'delete', 'fixture-veth-a')
    for path in ('/proc/sysrq-trigger', '/proc/kcore', '/proc/keys', '/proc/timer_list', '/proc/kmsg'):
        if Path(path).exists(): bind('/dev/null', path)
    # Bind only the pinned delegated subtree. A fresh cgroup2 mount can reject
    # differing host superblock options; this FD bind preserves those options
    # while making every host ancestor inaccessible in the fixture mount tree.
    try:
        bind('/proc/self/fd/' + str(cgroup_fd), '/sys/fs/cgroup', writable=True)
        observed = Path('/sys/fs/cgroup').stat()
        require((observed.st_dev, observed.st_ino) == (cgroup_identity.st_dev, cgroup_identity.st_ino), 'Cgroup FD bind identity changed')
    finally: os.close(cgroup_fd)
    require(Path('/sys/fs/cgroup/cgroup.procs').read_text().strip() == '', 'Delegated cgroup root unexpectedly populated')
    require(Path('/proc/self/cgroup').read_text().strip() == '0::/supervisor', 'Unexpected private cgroup root')
    for source, target in ((root / 'work', WORK), (root / 'store', STORE), (root / 'state', Path('/run/anvildroid-system')),
                           (root / 'home', Path(value['home']))):
        target.mkdir(mode=0o755, parents=True, exist_ok=True); bind(source, target, writable=True)
    bind(root / 'package/usr/local/lib/anvildroid-controller', ASSETS)
    # Refuse global module loading even if stock ashmem probing calls modprobe.
    refusal = root / 'refuse-modprobe'; refusal.write_text('#!/bin/sh\necho "Disposable Default: module loading disabled" >&2\nexit 1\n'); refusal.chmod(0o755)
    modprobe = Path(shutil.which('modprobe')).resolve(); bind(refusal, modprobe)
    # Namespaced networking still needs iptables/nft tools; host service control
    # is blocked by private /run, no host service manager sockets are rebound.
    require(not Path('/run/systemd/private').exists() and not Path('/run/dbus/system_bus_socket').exists(), 'Host control sockets visible')
    caps = int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapBnd:')), 16)
    require(all(not caps & (1 << bit) for bit in DROPPED_CAPS.values()), 'Global kernel capabilities retained')
    # Ordinary device inodes must differ from the originals even though rdev is
    # reused. Binder nodes come from an entirely new private BinderFS instance.
    for name, before in value['host_devices'].items():
        after = identity('/dev/' + name)
        require((after['device'], after['inode']) != (before['device'], before['inode']), 'Host device inode reused: ' + name)
    require(os.statvfs('/').f_flag & os.ST_RDONLY, 'Host root is writable')
    return {'namespaces': current, 'cap_bounding': hex(caps), 'private_cgroup': Path('/proc/self/cgroup').read_text().strip(),
            'host_control_sockets_visible': False, 'host_root_read_only': bool(os.statvfs('/').f_flag & os.ST_RDONLY),
            'device_policy': policy, 'writable_sysctls': list(NET_SYSCTLS), 'writable_private_netns_sysctl_trees': list(NET_SYSCTL_TREES), 'dynamic_veth_sysctl_probe': True, 'loops': value['loops'],
            'cgroup_root_identity': identity('/sys/fs/cgroup'), 'private_proc_pid1': True, 'process_local_proc_writable': True, 'protected_global_proc_entries': sorted(proc_protected)}


def configure_stock(root, value):
    # Call installed stock generators; do not copy or replace their lifecycle.
    launcher = Path('/usr/bin/waydroid').resolve(strict=True); sys.path.insert(0, str(launcher.parent))
    import tools
    tools.helpers.logging.add_verbose_log_level()
    args = argparse.Namespace(details_to_stdout=False); tools.prep_args(args)
    args.vendor_type = 'MAINLINE'; args.BINDER_DRIVER = 'anvil-fixture-binder'
    args.VNDBINDER_DRIVER = 'anvil-fixture-vndbinder'; args.HWBINDER_DRIVER = 'anvil-fixture-hwbinder'
    names = list(BINDER_NAMES)
    for name in names: Path('/dev', name).symlink_to('/dev/binderfs/' + name)
    # Stock do_start independently checks its supported discovery names.
    # Alias only its actual preferred names to the SAME private devices, so
    # its probe observes existing nodes instead of stacking a new binderfs.
    for candidates, name in zip((tools.helpers.drivers.BINDER_DRIVERS, tools.helpers.drivers.VNDBINDER_DRIVERS, tools.helpers.drivers.HWBINDER_DRIVERS), names):
        alias = candidates[0]
        require(re.fullmatch(r'[A-Za-z0-9_-]+', alias), 'Unsafe installed stock Binder discovery name')
        Path('/dev', alias).symlink_to('/dev/binderfs/' + name)
    config = ('[waydroid]\narch=x86_64\nvendor_type=MAINLINE\nimages_path=/var/lib/waydroid/images\nmount_overlays=True\n'
              'binder=' + names[0] + '\nvndbinder=' + names[1] + '\nhwbinder=' + names[2] + '\nsystem_ota=None\nvendor_ota=None\n'
              '\n[properties]\nro.hardware.gralloc=default\nro.hardware.egl=angle\nro.hardware.vulkan=pastel\nwaydroid.stub_sensors_hal=1\nro.waydroid.software_rendering=1\nro.waydroid.override_props=false\ndebug.hwui.renderer=skiagl\n')
    (WORK / 'waydroid.cfg').write_text(config)
    cfg = tools.config.load(args)
    args.images_path = cfg['waydroid']['images_path']; args.system_ota = cfg['waydroid']['system_ota']; args.vendor_ota = cfg['waydroid']['vendor_ota']
    tools.helpers.lxc.make_base_props(args)
    before_binder = {name: identity('/dev/binderfs/' + name) for name in (*BINDER_NAMES, 'binder-control')}
    require(tools.helpers.drivers.probeBinderDriver(args) == 0, 'Stock Binder discovery failed')
    require(before_binder == {name: identity('/dev/binderfs/' + name) for name in before_binder}, 'Stock probe replaced private BinderFS')
    tools.helpers.lxc.set_lxc_config(args)
    mount_probes = {}
    for name in ('system', 'vendor'):
        target = root / ('mount-probe-' + name); target.mkdir(exist_ok=True)
        tools.helpers.mount.mount(args, str(WORK / 'images' / (name + '.img')), str(target))
        try:
            require(os.statvfs(target).f_flag & os.ST_RDONLY, 'Stock copied image mount is writable')
            mount_probes[name] = {'mounted_read_only': True,
                'angle': (target / 'lib64/egl/libEGL_angle.so').is_file(),
                'pastel': (target / 'lib64/hw/vulkan.pastel.so').is_file(),
                'swiftshader': (target / 'lib64/egl/libEGL_swiftshader.so').is_file()}
        finally: run('umount', target)
    path = WORK / 'lxc/waydroid/config'; text = path.read_text()
    # Explicit TEST-ONLY restrictions: no global time/boot/module/raw-IO caps;
    # LXC cgroup administration sees only the delegated fixture root.
    lines = []
    for line in text.splitlines():
        if line.startswith('lxc.console.path'): continue
        if line.startswith('lxc.cap.keep'):
            key, caps = line.split('=', 1)
            line = key + '= ' + ' '.join(cap for cap in caps.split() if cap not in DROPPED_CAPS)
        lines.append(line)
    hook = root / 'audit-start'
    hook.write_text('#!/bin/sh\nexec /usr/bin/python3 ' + shlex.quote(str(SCRIPT)) + ' ' + shlex.quote(str(root)) + ' --audit-start\n')
    hook.chmod(0o700)
    lines += ['lxc.console.logfile = ' + str(root / 'android-console.log'), 'lxc.console.size = 1MB', 'lxc.log.file = ' + str(root / 'lxc-debug.log'), 'lxc.log.level = DEBUG', 'lxc.cgroup.dir = android', 'lxc.cgroup.relative = 0', 'lxc.hook.pre-start = ' + str(hook)]
    path.write_text('\n'.join(lines) + '\n')
    policy = audit_lxc(WORK / 'lxc/waydroid', value['home'], str(hook))
    return {'stock_tools': str(launcher.parent), 'binder': {name: identity('/dev/' + name) for name in names}, 'lxc_policy': policy, 'stock_image_mount_probes': mount_probes}


def audit_lxc(directory, home='/home/acceptance', hook=None):
    result = {}
    for name in ('config', 'config_nodes', 'config_session'):
        path = directory / name; text = path.read_text(); result[name] = hashlib.sha256(text.encode()).hexdigest()
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith('#'): continue
            key, _, value = line.partition('='); key, value = key.strip(), value.strip()
            if key.startswith('lxc.hook.'):
                require((key == 'lxc.hook.post-stop' and value == '/dev/null') or
                        (key == 'lxc.hook.pre-start' and hook is not None and value == hook), 'Unexpected stock LXC hook')
            if key == 'lxc.include': require(value in [str(directory / 'config_nodes'), str(directory / 'config_session')], 'Unexpected stock LXC include')
            if key == 'lxc.cap.keep': require(not set(value.split()) & DROPPED_CAPS.keys(), 'Unsafe guest capabilities')
            if key.startswith('lxc.cgroup.dir'): require(value == 'android', 'LXC cgroup path escapes delegation')
            require(not key.startswith('lxc.namespace.') and key != 'lxc.mount.fstab', 'Unexpected external namespace or mount table')
            if key.startswith('lxc.cgroup'):
                require(key in ('lxc.cgroup.dir', 'lxc.cgroup.relative'), 'Unexpected cgroup directive')
                if key == 'lxc.cgroup.relative': require(value == '0', 'Unexpected cgroup placement policy')
            if key == 'lxc.rootfs.path': require(value in (str(WORK / 'rootfs'), 'dir:' + str(WORK / 'rootfs')), 'Unexpected rootfs path')
            if key == 'lxc.mount.entry':
                fields = value.split(); require(len(fields) == 6, 'Invalid LXC mount entry')
                source, target, fs, options = fields[:4]
                require('..' not in Path(source).parts, 'Mount source traversal')
                require(target.strip('/') and '..' not in Path(target).parts, 'Mount target traversal')
                if Path(target).is_absolute():
                    require(target in ('/run/xdg', '/run/xdg/pulse') and source == 'tmpfs' and fs == 'none' and options == 'create=dir', 'Unexpected absolute LXC mount target')
                require(fs in ('none', 'tmpfs', 'devpts'), 'Unexpected LXC mount filesystem')
                allowed_options = {'bind', 'rbind', 'ro', 'rw', 'nosuid', 'nodev', 'noexec', 'optional', 'defaults', 'create=file', 'create=dir'}
                require(all(option in allowed_options or option.startswith(('mode=', 'ptmxmode=')) for option in options.split(',')), 'Unexpected LXC mount options')
                allowed = (source in ('tmpfs', 'none') or source.startswith('/dev/') or
                           source.startswith('/var/lib/waydroid/') or source.startswith('/run/anvildroid-system/shares-') or
                           source in ('/sys/module/lowmemorykiller', '/sys/kernel/debug', '/sys/class/leds/vibrator', '/sys/devices/virtual/timed_output/vibrator') or
                           source == home + '/data' or source.startswith(home + '/runtime/'))
                require(allowed, 'Host control/session mount requested: ' + source)
    return result


def audit_proc_globals():
    protected = []
    require(os.readlink('/proc/self') == str(os.getpid()), 'Unexpected proc self alias')
    require(os.readlink('/proc/thread-self') == str(os.getpid()) + '/task/' + str(os.getpid()), 'Unexpected proc thread alias')
    for entry in Path('/proc').iterdir():
        if entry.name.isdecimal() or entry.name in ('self', 'thread-self'): continue
        if entry.name in ('net', 'mounts'):
            require(entry.is_symlink() and os.readlink(entry) == 'self/' + entry.name, 'Unexpected process-local proc alias')
            continue
        require(not entry.is_symlink() and os.statvfs(entry).f_flag & os.ST_RDONLY, 'Global proc entry became writable: ' + entry.name)
        protected.append(entry.name)
    require(not os.statvfs('/proc/self/attr').f_flag & os.ST_RDONLY, 'Process-local proc attributes became read-only')
    return sorted(protected)


def audit_start(root):
    value = metadata(root)
    require(Path('/proc/self/cgroup').read_text().strip().startswith('0::/'), 'Missing private cgroup identity')
    require(namespaces()['net'] != value['host_namespaces']['net'] and namespaces()['pid'] != value['host_namespaces']['pid'], 'Host namespace reused at LXC start')
    require(not Path('/run/systemd/private').exists(), 'Host manager visible at LXC start')
    # Re-read the final files after stock session generation and production
    # sharing rewrite, immediately before LXC starts the Android init process.
    report = audit_lxc(WORK / 'lxc/waydroid', value['home'], str(root / 'audit-start'))
    write_json(root / 'final-lxc-audit.json', {'fixture_id': value['id'], 'files': report, 'protected_global_proc_entries': audit_proc_globals(), 'time': time.time()})


def private_bus(root):
    config = root / 'dbus.conf'
    config.write_text('<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">\n'
        '<busconfig><type>system</type><listen>unix:path=/run/private-system-bus</listen><auth>EXTERNAL</auth>'
        '<policy context="default"><allow user="*"/><allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/></policy></busconfig>\n')
    log = (root / 'dbus.log').open('a')
    process = subprocess.Popen(['dbus-daemon', '--nofork', '--config-file=' + str(config)], stdout=log, stderr=log)
    deadline = time.monotonic() + 5
    while not Path('/run/private-system-bus').exists():
        require(process.poll() is None and time.monotonic() < deadline, 'Private DBus failed'); time.sleep(.05)
    os.environ['DBUS_SYSTEM_BUS_ADDRESS'] = 'unix:path=/run/private-system-bus'
    return process


def spawn(root, name, command):
    log = (root / (name + '.log')).open('a')
    return subprocess.Popen(list(map(str, command)), stdout=log, stderr=log)


def stop_process(process):
    if process is None or process.poll() is not None: return
    process.terminate()
    try: process.wait(timeout=5)
    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=3)


def validate_init_cgroup(text):
    lines = text.strip().splitlines()
    require(len(lines) == 1 and lines[0].startswith('0::/'), 'Unexpected init cgroup format')
    path = lines[0][3:]
    require('..' not in Path(path).parts and (path == '/android' or path.startswith('/android/')
            or path == '/supervisor/android' or path.startswith('/supervisor/android/')), 'Android init escaped its fixture cgroup')
    return path


def controller_request(value, request):
    client = '''import json,socket,sys
s=socket.socket(socket.AF_UNIX); s.settimeout(15); s.connect('/run/anvildroid/control.sock')
s.sendall(sys.stdin.buffer.read()+b'\\n'); print(s.makefile().readline())
'''
    result = subprocess.run([sys.executable, '-c', client], input=json.dumps(request), text=True,
        capture_output=True, timeout=20, check=True, user=value['owner_uid'], group=value['owner_gid'], extra_groups=[])
    response = json.loads(result.stdout); require(response.get('ok'), 'Production controller rejected request: ' + str(response))
    return response['result']


def prepare_audio(root, value, processes):
    home = Path(value['home'])
    env = dict(os.environ, HOME=str(home), XDG_RUNTIME_DIR=str(home / 'runtime'))
    audio_config = home / 'audio-config'; audio_config.mkdir(exist_ok=True)
    os.chown(audio_config, value['owner_uid'], value['owner_gid'])
    (audio_config / 'fixture-core.conf').write_text('context.properties = { core.daemon = true core.name = pipewire-0 support.dbus = false }\ncontext.spa-libs = { audio.convert.* = audioconvert/libspa-audioconvert support.* = support/libspa-support }\ncontext.modules = [ { name = libpipewire-module-protocol-native } { name = libpipewire-module-client-node } { name = libpipewire-module-adapter } { name = libpipewire-module-access args = { access.force = unrestricted } } ]\n')
    pulse_socket = home / 'runtime/pulse/native'; pulse_socket.parent.mkdir(exist_ok=True)
    os.chown(pulse_socket.parent, value['owner_uid'], value['owner_gid'])
    (audio_config / 'fixture-pulse.conf').write_text('context.properties = { support.dbus = false }\ncontext.spa-libs = { audio.convert.* = audioconvert/libspa-audioconvert support.* = support/libspa-support }\ncontext.modules = [ { name = libpipewire-module-protocol-pulse args = { server.address = [ "unix:' + str(pulse_socket) + '" ] } } ]\n')
    audio_env = dict(env, PIPEWIRE_CONFIG_DIR=str(audio_config), PIPEWIRE_RUNTIME_DIR=str(home / 'runtime'), PIPEWIRE_REMOTE='pipewire-0')
    for label, command in (('audio-core', ['pipewire', '-c', 'fixture-core.conf']), ('audio-pulse', ['pipewire-pulse', '-c', 'fixture-pulse.conf'])):
        audio_log = (root / (label + '.log')).open('ab')
        try:
            process = subprocess.Popen(command, env=audio_env, user=value['owner_uid'], group=value['owner_gid'], extra_groups=[], stdout=audio_log, stderr=audio_log)
        finally: audio_log.close()
        processes.append(process)
        target = home / 'runtime/pipewire-0' if label == 'audio-core' else pulse_socket
        deadline = time.monotonic() + 10
        while not target.is_socket():
            require(process.poll() is None and time.monotonic() < deadline, 'Private real audio server failed: ' + label); time.sleep(.1)


REMOVED_TARGETS = ('/data/media/0/AnvilDroid/Acceptance',
                   '/mnt/pass_through/0/emulated/0/AnvilDroid/Acceptance',
                   '/sdcard/AnvilDroid/Acceptance')


def verify_removed_mounts(mountinfo):
    require(mountinfo.strip() and len(mountinfo) <= 1024 * 1024, 'Removal mountinfo empty or exceeds bound')
    points = []
    for line in mountinfo.splitlines():
        fields = line.split()
        require(len(fields) >= 10 and '-' in fields[6:], 'Malformed guest mountinfo')
        point = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), fields[4])
        require(point not in REMOVED_TARGETS, 'Removed source still mounted at ' + point)
        points.append(point)
    return points


def verify_removed_target(kind, empty, sentinel):
    require(not sentinel, 'Removed source sentinel remains visible')
    require(kind == 'absent' or (kind == 'directory' and empty),
            'Removed target must be absent or an ordinary empty directory')


def run_lifecycle(root, value, proxy, service, processes, report):
    import tools
    spec = importlib.util.spec_from_file_location('acceptance_linux', ASSETS / 'runtime-linux.py')
    linux = importlib.util.module_from_spec(spec); spec.loader.exec_module(linux)
    home = Path(value['home']); token = value['share_token']; events = []
    def record(phase, **fields):
        events.append(dict(phase=phase, time=time.time(), **fields)); write_json(root / 'lifecycle.json', {'events': events})
    def request(op, **fields): return controller_request(value, dict(op=op, id='default', **fields))
    def android(command, timeout=20, stdin=subprocess.DEVNULL):
        return linux.run_bounded('lxc-attach', '-P', str(WORK / 'lxc'), '-n', 'waydroid', '--',
            '/system/bin/sh', '-c', command, timeout=timeout, stdin=stdin, operation='Disposable Default acceptance', full_output=True, output_limit=1024 * 1024)
    def stopped():
        result = run('lxc-info', '-P', WORK / 'lxc', '-n', 'waydroid', '-sH', capture_output=True, text=True)
        require(result.stdout.strip() == 'STOPPED', 'Stock stop did not positively complete')
        require(dict(proxy.GetSession(timeout=3)) == {}, 'Stock session remains after stop')
        marker = Path('/run/anvildroid-system/shares-session.json')
        require(not marker.exists(), 'Production session marker remains')
        fd = os.open(STORE / 'default-shares.lock', os.O_RDWR | os.O_NOFOLLOW)
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally: os.close(fd)
        require(os.readlink('/proc/' + str(service.pid) + '/ns/mnt') == report['service_marker']['mount_namespace'], 'Service did not return to its baseline namespace')
    def stop():
        try: proxy.Stop(False, timeout=45)
        except Exception:
            holders = []; service_ns = os.readlink('/proc/' + str(service.pid) + '/ns/mnt')
            for base in (Path('/proc'), Path('/proc', str(service.pid), 'task')):
                for path in base.iterdir():
                    if not path.name.isdecimal(): continue
                    try:
                        if os.readlink(path / 'ns/mnt') == service_ns:
                            holders.append({'kind': 'thread' if base.name == 'task' else 'process', 'pid': int(path.name),
                                'cmdline': (path / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')[:1000],
                                'status': (path / 'status').read_text()[:2500], 'stat': (path / 'stat').read_text(),
                                'mount_namespace': service_ns})
                    except (FileNotFoundError, ProcessLookupError): pass
            write_json(root / 'stop-failure-holders.json', {'holders': holders})
            raise
        stopped(); record('stopped')
    def diagnostics(phase):
        binary = REPO / 'target/debug/examples/acceptance-diagnostics'
        require(binary.is_file(), 'Build the production diagnostic exporter first')
        folder = home / ('reports-' + phase); folder.mkdir(mode=0o700, exist_ok=True); os.chown(folder, value['owner_uid'], value['owner_gid'])
        result = subprocess.run([str(binary), 'default', str(folder)], capture_output=True, text=True, timeout=60,
            user=value['owner_uid'], group=value['owner_gid'], extra_groups=[])
        require(result.returncode == 0, 'Actual core diagnostics acceptance failed: ' + result.stderr[-1000:])
        record('diagnostics-' + phase, output=result.stdout[-2000:])
    env = dict(os.environ, HOME=str(home), XDG_RUNTIME_DIR=str(home / 'runtime'), LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe')
    log = (root / 'weston.log').open('ab')
    try:
        weston = subprocess.Popen(['weston', '--no-config', '--backend=headless-backend.so', '--renderer=gl', '--socket=fixture-wayland', '--idle-time=0'],
            env=env, user=value['owner_uid'], group=value['owner_gid'], extra_groups=[], stdout=log, stderr=log)
    finally: log.close()
    processes.append(weston)
    deadline = time.monotonic() + 20
    while not (home / 'runtime/fixture-wayland').is_socket():
        require(weston.poll() is None and time.monotonic() < deadline, 'Private software Weston failed'); time.sleep(.1)
    session = dict(tools.config.session_defaults)
    session.update(user_name=value['owner_name'], user_id=str(value['owner_uid']), group_id=str(value['owner_gid']),
        host_user=str(home), pid=str(weston.pid), waydroid_data=str(home / 'data'), waydroid_user_state=str(home),
        xdg_data_home=str(home), xdg_runtime_dir=str(home / 'runtime'), wayland_display='fixture-wayland',
        pulse_runtime_path=str(home / 'runtime/pulse'), lcd_density='0', background_start='true')
    def start(phase):
        proxy.Start(session, timeout=60)
        pid = int(run('lxc-info', '-P', WORK / 'lxc', '-n', 'waydroid', '-pH', capture_output=True, text=True).stdout.strip())
        cgroup = validate_init_cgroup(Path('/proc', str(pid), 'cgroup').read_text())
        require(os.readlink('/proc/' + str(pid) + '/ns/mnt') != os.readlink('/proc/self/ns/mnt'), 'Android has no private mount namespace')
        record('init-' + phase, pid=pid, starttime=Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19], cgroup=cgroup)
        init_start = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]
        init_mnt = os.readlink('/proc/' + str(pid) + '/ns/mnt')
        deadline = time.monotonic() + 180; last_error = None
        while True:
            require(Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19] == init_start and
                    os.readlink('/proc/' + str(pid) + '/ns/mnt') == init_mnt and
                    validate_init_cgroup(Path('/proc', str(pid), 'cgroup').read_text()) == cgroup,
                    'Android init identity changed during boot')
            require(run('lxc-info', '-P', WORK / 'lxc', '-n', 'waydroid', '-sH', capture_output=True, text=True).stdout.strip() == 'RUNNING', 'Android stopped during boot')
            try:
                if android('getprop sys.boot_completed', timeout=10).strip() == '1': break
            except RuntimeError as error: last_error = str(error)[-1500:]
            if time.monotonic() >= deadline:
                diagnostics = {'last_attach_error': last_error}
                for label, command in (('properties', 'getprop'), ('logcat', 'logcat -d -t 500'), ('processes', 'ps -A')):
                    try: diagnostics[label] = android(command, timeout=10)[-64000:]
                    except RuntimeError as error: diagnostics[label + '_error'] = str(error)[-1500:]
                write_json(root / ('boot-failure-' + phase + '.json'), diagnostics)
                raise RuntimeError('Disposable Android boot timed out')
            time.sleep(2)
        record('boot-' + phase)
    def mounted():
        deadline = time.monotonic() + 300
        while True:
            status = request('shares_get')
            if any(x.get('name') == 'Acceptance' and x.get('mounted') for x in status.get('shares', [])): return status
            require(time.monotonic() < deadline, 'Production share mounted readback timed out: ' + str(status)); time.sleep(2)
    def probe(phase, writable, absent=False):
        snapshot = {p.name: p.read_bytes() for p in (home / 'share').iterdir() if p.is_file()}
        if absent:
            removal = {'shares': request('shares_get'), 'targets': []}
            write_json(root / 'removal-metadata.json', removal)
            require(removal['shares'].get('shares') == [], 'Removed share still configured')
            removal['mountinfo'] = android('cat /proc/self/mountinfo')
            write_json(root / 'removal-metadata.json', removal)
            verify_removed_mounts(removal['mountinfo'])
            for target in REMOVED_TARGETS:
                command = ('p=' + shlex.quote(target) + '; '
                    'if [ -L "$p" ]; then echo symlink; elif [ -d "$p" ]; then echo directory; '
                    'elif [ -e "$p" ]; then echo other; else echo absent; fi; '
                    'if [ -d "$p" ]; then if entries=$(ls -A "$p"); then '
                    'if [ -n "$entries" ]; then echo nonempty; else echo empty; fi; '
                    'else echo read-error; fi; else echo empty; fi; '
                    'if [ -e "$p/acceptance-read" ] || [ -L "$p/acceptance-read" ]; then echo sentinel; else echo no-sentinel; fi; '
                    'ls -ldn "$p" 2>&1 || true')
                output = android(command)
                item = {'target': target, 'metadata': output[:4096]}
                removal['targets'].append(item); write_json(root / 'removal-metadata.json', removal)
                fields = output.splitlines(); require(len(fields) >= 3, 'Incomplete removal path metadata')
                require(fields[1] in ('empty', 'nonempty'), 'Cannot read removed target directory')
                verify_removed_target(fields[0], fields[1] == 'empty', fields[2] == 'sentinel')
        for kind, base in (('raw', '/data/media/0/AnvilDroid/Acceptance'), ('fuse', '/sdcard/AnvilDroid/Acceptance')):
            marker = 'acceptance-write-' + token + '-' + phase + '-' + kind
            script = 'p=' + shlex.quote(base) + '; '
            if absent: script += 'test ! -e "$p/acceptance-read"; test ! -L "$p/acceptance-read"'
            else:
                script += 'test "$(cat "$p/acceptance-read")" = ' + shlex.quote(token) + '; '
                if writable: script += 'printf %s ' + shlex.quote(token) + ' > "$p/' + marker + '"'
                else: script += 'if (printf x > "$p/' + marker + '") 2>/data/local/tmp/share-error; then exit 41; fi; grep -qi "read-only file system" /data/local/tmp/share-error'
            android('set -e; ' + script)
            if writable: require((home / 'share' / marker).read_text() == token, 'Guest write did not reach exact fixture source')
        require(digest(root / 'probe.apk') == value['probe_apk_sha256'], 'Probe APK changed')
        with (root / 'probe.apk').open('rb') as apk: android('cat > /data/local/tmp/anvil-share-probe.apk', stdin=apk)
        android('pm install -r /data/local/tmp/anvil-share-probe.apk', timeout=40)
        details = android('dumpsys package org.anvildroid.touchprobe')
        require('targetSdk=28' in details, 'Probe does not use actual SDK28 app identity')
        android('pm grant org.anvildroid.touchprobe android.permission.READ_EXTERNAL_STORAGE; pm grant org.anvildroid.touchprobe android.permission.WRITE_EXTERNAL_STORAGE')
        app_token = token  # Removal must detect the original source sentinel, too.
        android('logcat -c; am start -n org.anvildroid.touchprobe/.MainActivity -a org.anvildroid.touchprobe.SHARE_PROBE --es share Acceptance --es token ' + shlex.quote(app_token))
        deadline = time.monotonic() + 30
        while True:
            logs = android('logcat -d -s AnvilTouchProbe:I')
            lines = [line for line in logs.splitlines() if 'SHARE ' in line]
            if any('SHARE write=' in line for line in lines): break
            require(time.monotonic() < deadline, 'Actual application share probe timed out'); time.sleep(1)
        result = '\n'.join(lines)
        match = re.search(r'SHARE uid=(\d+)', result)
        require(match and int(match.group(1)) >= 10000, 'Probe did not run as actual app UID')
        if not writable and not absent: require('EROFS' in result or 'Read-only file system' in result, 'App RO failure was not EROFS')
        require('read=' + str(not absent).lower() in result and 'write=' + str(writable).lower() in result, 'Actual app share result mismatch: ' + result)
        if writable: require((home / 'share' / ('acceptance-write-' + app_token + '-app')).read_text() == app_token, 'Application write not reflected in fixture source')
        if not writable: require(snapshot == {p.name: p.read_bytes() for p in (home / 'share').iterdir() if p.is_file()}, 'Read-only or removed share changed source')
        record('probe-' + phase, application=result, mounted=False if absent else mounted())
    start('claim'); request('existing_claim'); stop(); diagnostics('stopped')
    request('shares_set', name='Acceptance', source=str(home / 'share'), read_only=True)
    start('ro'); mounted(); probe('ro', False); diagnostics('running'); stop()
    request('shares_set', name='Acceptance', source=str(home / 'share'), read_only=False)
    start('rw'); mounted(); probe('rw', True)
    # A real process retaining the production service mount namespace must
    # prevent cleanup and keep configuration locked until that helper exits.
    helper = subprocess.Popen(['nsenter', '-t', str(service.pid), '-m', '--', 'sleep', '90'])
    processes.append(helper)
    try:
        deadline = time.monotonic() + 5
        while os.readlink('/proc/' + str(helper.pid) + '/ns/mnt') != os.readlink('/proc/' + str(service.pid) + '/ns/mnt'):
            require(helper.poll() is None and time.monotonic() < deadline, 'Retained namespace helper failed'); time.sleep(.05)
        rejected = False
        try: proxy.Stop(False, timeout=45)
        except Exception as error: rejected = True; record('retained-helper-stop-rejected', error=str(error)[:1000])
        require(rejected, 'Stop discarded a retained production namespace')
        require(run('lxc-info', '-P', WORK / 'lxc', '-n', 'waydroid', '-sH', capture_output=True, text=True).stdout.strip() == 'STOPPED', 'Retained-helper Stop did not stop Android')
        rejected = False
        try: request('shares_remove', name='Acceptance')
        except RuntimeError: rejected = True
        require(rejected, 'Configuration lock was released while a helper retained the namespace')
        record('retained-helper-configuration-rejected')
    finally: stop_process(helper)
    stop()
    request('shares_remove', name='Acceptance')
    start('removed'); probe('removed', False, absent=True); stop()
    record('complete', clean_stock_stop=True)


def inside(args):
    root = args.root; value = metadata(root)
    require(args.host_proc_fd is not None, 'Original proc identity FD required')
    fd = os.open('self/stat', os.O_RDONLY, dir_fd=args.host_proc_fd)
    try: host_stat = os.read(fd, 4096).decode()
    finally: os.close(fd); os.close(args.host_proc_fd)
    supervisor = {'host_pid': int(host_stat.split()[0]), 'starttime': host_stat.rsplit(')', 1)[1].split()[19]}
    # Do not inherit any host desktop, agent or bus endpoint, including ones
    # located outside the masked conventional session directories.
    for key in list(os.environ):
        if key.startswith(('DBUS_', 'SSH_', 'PIPEWIRE_', 'PULSE_')) or key in ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'PULSE_SERVER', 'PIPEWIRE_REMOTE', 'GPG_AGENT_INFO'):
            os.environ.pop(key, None)
    processes = []
    try:
        report = mount_fixture(root, value)
        report['protected_global_proc_entries'] = audit_proc_globals()
        report.update(configure_stock(root, value))
        network_log = (root / 'stock-network-preflight.log').open('ab')
        try:
            run('/bin/sh', '-x', '/usr/lib/waydroid/data/scripts/waydroid-net.sh', 'start', stdout=network_log, stderr=network_log)
            run('/bin/sh', '-x', '/usr/lib/waydroid/data/scripts/waydroid-net.sh', 'stop', stdout=network_log, stderr=network_log)
        finally: network_log.close()
        report.update(verify_copies(root, value))
        prepare_audio(root, value, processes)
        report['private_audio_socket'] = True
        bus = private_bus(root); processes.append(bus)
        marker_path = Path('/run/anvildroid-system/integration.json')
        marker_path.unlink(missing_ok=True)  # Stale record belongs only to this inactive disposable fixture.
        service = spawn(root, 'runtime-system', [sys.executable, '-B', ASSETS / 'runtime-system.py']); processes.append(service)
        deadline = time.monotonic() + 10
        while not marker_path.exists():
            require(service.poll() is None and time.monotonic() < deadline, 'Private stock service failed'); time.sleep(.1)
        marker = json.loads(marker_path.read_text())
        require(marker['pid'] == service.pid and marker['mount_namespace'] != os.readlink('/proc/self/ns/mnt'), 'Unexpected production integration identity')
        import dbus
        deadline = time.monotonic() + 10
        while True:
            try:
                proxy = dbus.Interface(dbus.SystemBus().get_object('id.waydro.Container', '/ContainerManager', introspect=False), 'id.waydro.ContainerManager')
                require(dict(proxy.GetSession(timeout=2)) == {}, 'Preflight service already has a session'); break
            except dbus.DBusException:
                require(service.poll() is None and time.monotonic() < deadline, 'Private service bus did not become ready'); time.sleep(.1)
        Path('/run/anvildroid').mkdir(mode=0o755)
        controller = spawn(root, 'controller', [sys.executable, '-B', ASSETS / 'runtime-controller.py', '--store', STORE,
            '--socket', '/run/anvildroid/control.sock', '--allow-temporary-storage']); processes.append(controller)
        deadline = time.monotonic() + 10
        while not Path('/run/anvildroid/control.sock').exists():
            require(controller.poll() is None and time.monotonic() < deadline, 'Private controller failed'); time.sleep(.1)
        report.update({'mode': 'preflight', 'android_started': False, 'fixture_id': value['id'], 'fixture_root': str(root),
                       'harness_sha256': digest(SCRIPT), 'package_sha256': value['package_sha256'], 'probe_apk_sha256': value['probe_apk_sha256'], 'service_marker': marker,
                       'service_cgroup': Path('/proc', str(service.pid), 'cgroup').read_text().strip(),
                       'work_identity': identity(WORK), 'userdata_identity': identity(Path(value['home']) / 'data'),
                       'controller_endpoint': '/run/anvildroid/control.sock', 'bus_address': os.environ['DBUS_SYSTEM_BUS_ADDRESS'],
                       'pid1': os.getpid(), 'supervisor': supervisor, 'review_required_before_start': True})
        write_json(root / ('run-preflight.json' if args.run else 'preflight.json'), report)
        print(json.dumps(report), flush=True)
        if args.run:
            require(args.reviewed_preflight, 'A reviewed preflight digest is required')
            run_lifecycle(root, value, proxy, service, processes, report)
    finally:
        for process in reversed(processes): stop_process(process)
        # PID namespace destruction kills remaining descendants. No lazy host
        # path unmounts or installed service calls are used for cleanup.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ('prepare', 'restage', 'preflight', 'run'): mode.add_argument('--' + name, action='store_true')
    mode.add_argument('--audit-start', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--package', type=Path, default=REPO / 'target/releases/anvildroid-controller_0.1.3_amd64.deb')
    parser.add_argument('--probe-apk', type=Path, default=REPO / 'target/touch-probe/AnvilDroidTouchProbe.apk')
    parser.add_argument('--checkpoint-seconds', type=int, default=0)
    parser.add_argument('--host-proc-fd', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--expected-package-sha256')
    parser.add_argument('--image-source', type=Path, default=Path('/var/lib/waydroid/images'))
    parser.add_argument('--owner-uid', type=int, default=int(os.environ.get('SUDO_UID', '0')))
    parser.add_argument('--reviewed-preflight')
    parser.add_argument('--scoped', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(); require(os.geteuid() == 0, 'Root is required')
    require(0 <= args.checkpoint_seconds <= 600, 'Checkpoint bound invalid')
    if args.prepare: return prepare(args)
    if args.restage: return restage(args)
    if args.audit_start: return audit_start(args.root)
    value = metadata(args.root)
    if args.inside: return inside(args)
    if args.scoped: return scoped(args)
    if args.run:
        require(args.reviewed_preflight and args.reviewed_preflight == digest(args.root / 'preflight.json'), 'Reviewed preflight digest mismatch')
        prior = json.loads((args.root / 'preflight.json').read_text())
        require(prior['fixture_id'] == value['id'] and prior['fixture_root'] == str(args.root) and
                prior['package_sha256'] == value['package_sha256'] and prior['harness_sha256'] == digest(SCRIPT), 'Reviewed preflight identity or code changed')
        require(prior['probe_apk_sha256'] == value['probe_apk_sha256'] == digest(args.root / 'probe.apk'), 'Reviewed probe APK changed')
        copies = verify_copies(args.root, value)
        require(copies['copied_images'] == prior['copied_images'] and copies['payload_manifest_sha256'] == prior['payload_manifest_sha256'], 'Reviewed preflight image or payload provenance changed')
    require(digest(args.root / 'controller.deb') == value['package_sha256'], 'Pinned package changed')
    lock = os.open(args.root / 'launch.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    value['host_namespaces'] = namespaces()
    value['host_devices'] = {name: identity('/dev/' + name) for name in ('null', 'zero', 'fuse')}
    write_json(args.root / 'acceptance.json', value)
    before = value['host_devices']
    host_ipv6 = {str(p): p.read_text() for p in Path('/proc/sys/net/ipv6/conf').glob('*/disable_ipv6')}
    try:
        return subprocess.call(scope_command(args.root, value, 'run' if args.run else 'preflight', args.reviewed_preflight, args.checkpoint_seconds))
    finally:
        cgroup = Path(value.get('host_cgroup', '/sys/fs/cgroup/' + value['scope']))
        # scoped() writes the authenticated absolute path after delegation.
        latest = metadata(args.root); cgroup = Path(latest.get('host_cgroup', cgroup))
        require(cgroup.name == value['scope'] and str(cgroup).startswith('/sys/fs/cgroup/'), 'Unexpected teardown cgroup')
        deadline = time.monotonic() + 5
        while cgroup.exists() and 'populated 1' in (cgroup / 'cgroup.events').read_text():
            require(time.monotonic() < deadline, 'Fixture cgroup still populated; retaining task loops'); time.sleep(.1)
        clean_task_loops(args.root)
        write_json(args.root / 'teardown.json', {'scope': value['scope'], 'scope_empty': True, 'task_loops_detached': True, 'time': time.time()})
        require(host_ipv6 == {str(p): p.read_text() for p in Path('/proc/sys/net/ipv6/conf').glob('*/disable_ipv6')}, 'Host per-interface IPv6 state changed')
        write_json(args.root / 'host-ipv6-invariants.json', {'before': host_ipv6, 'after': {str(p): p.read_text() for p in Path('/proc/sys/net/ipv6/conf').glob('*/disable_ipv6')}})
        require(before == {name: identity('/dev/' + name) for name in before}, 'Original host device metadata changed')
        os.close(lock)


if __name__ == '__main__': sys.exit(main())
