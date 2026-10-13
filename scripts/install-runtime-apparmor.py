#!/usr/bin/python3
"""Grant Android apps access to the private Venus socket via a local override."""
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

RULE = b'  /run/anvil-venus/venus.sock rwk,\n'


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


def install(directory=Path('/etc/apparmor.d'), parser=None, run=subprocess.run):
    profile = directory / 'android_app'
    if not profile.exists():
        return 'No Android AppArmor profile; no override needed'
    trusted(directory, directory=True)
    trusted(profile)
    if not re.search(rb'^\s*#?include\s+(?:if exists\s+)?<local/android_app>\s*$',
                     profile.read_bytes(), re.MULTILINE):
        raise RuntimeError('Android AppArmor profile does not include local/android_app')
    parser = parser or shutil.which('apparmor_parser')
    if not parser:
        raise RuntimeError('apparmor_parser is required to validate the socket override')
    local = directory / 'local'
    local.mkdir(mode=0o755, exist_ok=True)
    trusted(local, directory=True)
    target = local / 'android_app'
    previous = None
    if target.exists() or target.is_symlink():
        trusted(target)
        previous = target.read_bytes()
    data = previous or b''
    if RULE.strip() in (line.strip() for line in data.splitlines()):
        return 'Venus socket AppArmor override already installed'
    data += (b'\n' if data and not data.endswith(b'\n') else b'')
    data += b'# AnvilDroid: private per-runtime Venus IPC; avoid per-frame audit traffic.\n' + RULE
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
    return 'Venus socket AppArmor override validated and loaded'


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Run this installer as root.')
    print(install())
