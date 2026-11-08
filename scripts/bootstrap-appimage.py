#!/usr/bin/python3
"""Idempotent first-run administrator bootstrap for the AppImage.

Installs or repairs the runtime tools, then starts the controller. Android
images are not installed here: users download or import them in Images.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

PAYLOAD = ('scripts', 'services', 'target', 'native', 'native-x11', 'packaging')
STAGE_PARENT = Path('/run')


def trusted(path):
    """True when path and every parent are root-owned and not group/world writable."""
    for item in (path, *path.parents):
        info = item.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return False
    return True


def stage(source, parent=STAGE_PARENT):
    """Copy the payload into a fresh root-owned 0700 directory.

    The AppImage mount (or the user's hand-over copy in /tmp) is owned or
    writable by the user; the controller installer only trusts root-owned
    private parents, so it must read from this copy instead."""
    target = Path(tempfile.mkdtemp(prefix='anvildroid-bootstrap-', dir=parent))
    try:
        for part in PAYLOAD:
            if (source / part).is_dir() and not (source / part).is_symlink():
                shutil.copytree(source / part, target / part, symlinks=True)
        for directory, folders, files in os.walk(target):
            for name in folders + files:
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                os.chown(path, 0, 0)
                os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o022)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def run(root):
    setup = root / 'scripts' / 'setup-waydroid.py'
    controller = root / 'scripts' / 'start-runtime-controller.py'
    if not setup.is_file() or not controller.is_file():
        print('The AppImage is missing its bootstrap payload.', file=sys.stderr)
        return 2
    # No-op when Waydroid, the host tools and its LXC templates are present.
    print('Checking Android runtime tools…', flush=True)
    code = subprocess.run(['/usr/bin/python3', '-B', str(setup), '--run-init']).returncode
    if code:
        print('Runtime tools setup failed; existing data was preserved.', file=sys.stderr)
        return code
    print('Installing and starting the AnvilDroid controller…', flush=True)
    return subprocess.run(['/usr/bin/python3', '-B', str(controller)]).returncode


def main():
    if os.geteuid() != 0:
        print('Bootstrap must run through pkexec as administrator.', file=sys.stderr)
        return 126
    source = Path(__file__).resolve().parent.parent
    if trusted(source):
        return run(source)
    staged = stage(source)
    try:
        return run(staged)
    finally:
        shutil.rmtree(staged, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
