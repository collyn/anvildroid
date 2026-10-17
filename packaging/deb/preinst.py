#!/usr/bin/python3 -I
"""Secure the private controller directory before dpkg unpacks privileged code."""
import os
from pathlib import Path
import stat
import sys

INSTALL = Path('/usr/local/lib/anvildroid-controller')
# The 0.1.2 archive also shipped these two resource sources as 0664. Keep this
# compatibility exception exact; arbitrary writable controller files fail.
LEGACY_RESOURCES = {Path('provision/AndroidManifest.xml'),
                    Path('provision/res/layout/decor_caption.xml')}


def secure_install_directory(path=INSTALL):
    """Repair root-owned 0775 directories inside the private install tree.

    Never chmod shared ancestors, follow symlinks, or adopt user-owned or
    world-writable directories. Pin each component while inspecting it.
    Validate files too; only the two known legacy resources may be repaired.
    Missing directories are created later by dpkg with the packaged modes.
    """
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise RuntimeError('Expected an absolute installation directory')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open('/', flags)
    current = Path('/')

    def validate(fd, current, repair=False, legacy_resource=False):
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if legacy_resource and mode == 0o664 and info.st_nlink != 1:
            raise RuntimeError(f'Unsafe hard-linked legacy controller resource: {current}')
        if info.st_uid == 0 and ((repair and mode == 0o775)
                                  or (legacy_resource and mode == 0o664)):
            os.fchmod(fd, 0o755 if repair else 0o644)
            info = os.fstat(fd)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError(
                f'Unsafe controller installation directory or asset: {current} '
                f'(uid={info.st_uid}, mode={stat.S_IMODE(info.st_mode):04o}); '
                'requires root ownership and no group/world write permission')
        return info

    def secure_tree(fd, current):
        # Harden parents before opening children. An unprivileged process can
        # no longer replace a checked child through an already secured parent.
        for name in os.listdir(fd):
            child_path = current / name
            child = None
            try:
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                dir_fd=fd)
                info = os.fstat(child)
                is_directory = stat.S_ISDIR(info.st_mode)
                if not is_directory and not stat.S_ISREG(info.st_mode):
                    raise RuntimeError(f'Unsafe controller installation directory or asset: {child_path}')
                validate(child, child_path, repair=is_directory,
                         legacy_resource=not is_directory and child_path.relative_to(path) in LEGACY_RESOURCES)
                if is_directory:
                    secure_tree(child, child_path)
            except OSError as error:
                raise RuntimeError(
                    f'Unsafe controller installation directory or asset: {child_path}') from error
            finally:
                if child is not None:
                    os.close(child)

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
            validate(directory, current, repair=current == path)
        secure_tree(directory, path)
    finally:
        os.close(directory)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in ('install', 'upgrade'):
        secure_install_directory()
