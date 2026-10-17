#!/usr/bin/python3 -I
"""Secure the private controller directory before dpkg unpacks privileged code."""
import os
from pathlib import Path
import stat
import sys

INSTALL = Path('/usr/local/lib/anvildroid-controller')


def secure_install_directory(path=INSTALL):
    """Repair only the root-owned 0775 directory left by older installs.

    Never chmod shared ancestors, follow symlinks, or adopt user-owned or
    world-writable directories. Pin each component while inspecting it.
    Missing directories are created later by dpkg with the packaged modes.
    """
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise RuntimeError('Expected an absolute installation directory')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open('/', flags)
    current = Path('/')
    try:
        for part in ('', *path.parts[1:]):
            if part:
                current /= part
                try:
                    child = os.open(part, flags, dir_fd=directory)
                except FileNotFoundError:
                    return
                except OSError as error:
                    raise RuntimeError(f'Unsafe controller installation directory: {current}') from error
                os.close(directory)
                directory = child
            info = os.fstat(directory)
            if current == path and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o775:
                os.fchmod(directory, 0o755)
                info = os.fstat(directory)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError(
                    f'Unsafe controller installation directory: {current} '
                    f'(uid={info.st_uid}, mode={stat.S_IMODE(info.st_mode):04o}); '
                    'requires root ownership and no group/world write permission')
    finally:
        os.close(directory)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in ('install', 'upgrade'):
        secure_install_directory()
