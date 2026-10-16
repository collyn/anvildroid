#!/usr/bin/python3
"""Install local Android IPC permissions and host-control restrictions."""
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

RULE = b'  /run/anvil-venus/venus.sock rwk,\n'
HOST_CONTROL_RULE = b'  deny /proc/sysrq-trigger w,\n'


def trusted(path, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise RuntimeError(f'Unsafe AppArmor policy path: {path}')


def replace(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.anvildroid-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), 0o644)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_override(directory, profile, name, rules, parser, run):
    trusted(profile.parent, directory=True)
    trusted(profile)
    if not re.search(rb'^\s*#?include\s+(?:if exists\s+)?<local/' + re.escape(name.encode()) + rb'>\s*$',
                     profile.read_bytes(), re.MULTILINE):
        raise RuntimeError('Android AppArmor profile does not include local/' + name)
    local = directory / 'local'
    local.mkdir(mode=0o755, exist_ok=True)
    trusted(local, directory=True)
    target = local / name
    previous = None
    if target.exists() or target.is_symlink():
        trusted(target)
        previous = target.read_bytes()
    data = previous or b''
    existing = {line.strip() for line in data.splitlines()}
    missing = [rule for rule in rules if rule.strip() not in existing]
    if not missing:
        return name + ' override already installed'
    data += (b'\n' if data and not data.endswith(b'\n') else b'')
    data += b'# AnvilDroid: private runtime IPC; Android must not signal host tasks via SysRq.\n'
    data += b''.join(missing)
    replace(target, data)
    try:
        run([parser, '-Q', '-T', str(profile)], check=True, capture_output=True, timeout=30)
        run([parser, '-r', '-T', str(profile)], check=True, capture_output=True, timeout=30)
    except Exception:
        if previous is None:
            target.unlink()
        else:
            replace(target, previous)
        raise
    return name + ' override validated and loaded'


def install(directory=Path('/etc/apparmor.d'), parser=None, run=subprocess.run):
    profiles = [(directory / 'lxc/lxc-waydroid', 'lxc-waydroid', (HOST_CONTROL_RULE,)),
                (directory / 'android_app', 'android_app', (RULE, HOST_CONTROL_RULE))]
    available = [(path, name, rules) for path, name, rules in profiles if path.exists()]
    if not available:
        return 'No Android AppArmor profile; no override needed'
    trusted(directory, directory=True)
    parser = parser or shutil.which('apparmor_parser')
    if not parser:
        raise RuntimeError('apparmor_parser is required to validate the runtime overrides')
    return '; '.join(install_override(directory, path, name, rules, parser, run)
                     for path, name, rules in available)


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Run this installer as root.')
    print(install())
